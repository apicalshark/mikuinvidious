import asyncio
import json
import uuid
import httpx
from urllib.parse import urlparse

from live_manager import live_manager
from quart import Blueprint, Response, request
from shared import Network, appconf, appcred, appredis, get_current_cred, image_limiter

proxy_bp = Blueprint("proxy", __name__)

COMMON_HEADERS = {
    "referer": "https://www.bilibili.com",
    "user-agent": "Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)",
}


async def render_proxy_pic(req_path):
    async with image_limiter:
        # Restore simple path handling from fb6c7792
        actual_path = req_path[11:]
        domain = actual_path.split("/")[0]

        if not (domain.endswith(".hdslb.com") or domain.endswith(".biliimg.com")):
            return Response("Forbidden", status=403)

        headers = COMMON_HEADERS.copy()
        url = f"https://{actual_path}"

        client = await Network.get_async_client()
        resp = None
        try:
            req = client.build_request("GET", url, headers=headers)
            resp = await client.send(req, follow_redirects=True)
            content = resp.content
            status_code = resp.status_code
            content_type = resp.headers.get("content-type")
            
            await resp.aclose()
            resp = None 

            return Response(content, status=status_code, content_type=content_type)
        except Exception as e:
            print(f"[Proxy] Error in render_proxy_pic for {url}: {e}")
            return Response(str(e), status=502)
        finally:
            if resp:
                await resp.aclose()


class ClosingIterator:
    """
    An async iterator wrapper that ensures the upstream response is closed
    when the iterator is closed or exhausted.
    """

    def __init__(self, generator, upstream_resp_func):
        self.generator = generator
        self.upstream_resp_func = upstream_resp_func

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await anext(self.generator)

    async def aclose(self):
        """Called by Quart/Hypercorn when the response is finished."""
        if hasattr(self.generator, "aclose"):
            await self.generator.aclose()
        resp = self.upstream_resp_func()
        if resp:
            await resp.aclose()

    async def close(self):
        """Standard close method for older ASGI compatibility."""
        await self.aclose()


def adjust_range_header(headers, bytes_sent):
    """Adjust the Range header for mid-stream resumption."""
    if bytes_sent <= 0:
        return headers
    
    range_val = headers.get("range")
    if not range_val:
        headers["range"] = f"bytes={bytes_sent}-"
        return headers
    
    try:
        prefix, rrange = range_val.split("=")
        parts = rrange.split("-")
        start = int(parts[0])
        end = parts[1] if len(parts) > 1 and parts[1] else ""
        headers["range"] = f"bytes={start + bytes_sent}-{end}"
    except Exception:
        pass
    return headers


class ProxyResponse(Response):
    """
    A specialized Response class that manages upstream httpx response(s) with CDN fallback and resumption.
    """

    def __init__(self, urls, headers, cookies, *args, **kwargs):
        self.urls = urls if isinstance(urls, list) else [urls]
        self.client_headers = headers.copy()
        self.cookies = cookies or {}
        self.upstream_resp = None
        self._current_url_index = 0
        
        # Pull status from kwargs if present (passed from probe)
        status = kwargs.get("status", 200)

        async def response_generator():
            bytes_sent = 0
            try:
                while self._current_url_index < len(self.urls):
                    url = self.urls[self._current_url_index]
                    client = await Network.get_stream_client()

                    try:
                        # Adjust range for resumption
                        current_headers = adjust_range_header(self.client_headers.copy(), bytes_sent)
                        proxy_request = client.build_request(
                            "GET", url, headers=current_headers, cookies=self.cookies
                        )
                        self.upstream_resp = await client.send(proxy_request, stream=True, follow_redirects=True)

                        if self.upstream_resp.status_code < 400:
                            print(f"[Proxy] Success with CDN {self._current_url_index} (Sent: {bytes_sent} bytes)")
                            try:
                                async for chunk in self.upstream_resp.aiter_bytes(chunk_size=8192):
                                    yield chunk
                                    bytes_sent += len(chunk)
                                return  # Finished successfully
                            except (httpx.HTTPError, asyncio.CancelledError, GeneratorExit) as e:
                                if isinstance(e, (GeneratorExit, asyncio.CancelledError)): raise
                                print(f"[Proxy] Mid-stream error on CDN {self._current_url_index}: {e}")
                            finally:
                                await self.upstream_resp.aclose()
                                self.upstream_resp = None
                        else:
                            await self.upstream_resp.aclose()
                            self.upstream_resp = None
                    except Exception as e:
                        print(f"[Proxy] CDN {self._current_url_index} failed: {e}")
                        if self.upstream_resp:
                            await self.upstream_resp.aclose()
                            self.upstream_resp = None

                    self._current_url_index += 1
                print(f"[Proxy] All CDNs exhausted. Total sent: {bytes_sent} bytes.")
            except (asyncio.CancelledError, GeneratorExit):
                raise
            finally:
                if self.upstream_resp:
                    await self.upstream_resp.aclose()
                    self.upstream_resp = None

        super().__init__(ClosingIterator(response_generator(), lambda: self.upstream_resp), *args, **kwargs)

    async def aclose(self):
        if hasattr(self.response, "aclose"):
            await self.response.aclose()
        await super().aclose()


@proxy_bp.route("/proxy/dash/<media_type>/<int:qn>")
async def proxy_dash(media_type, qn):
    cached_data = await appredis.get(f"miku_dash_url_{media_type}_{qn}")
    if not cached_data: return Response("Not Found", status=404)

    try:
        urls = json.loads(cached_data) if cached_data.startswith("[") else [cached_data]
    except Exception:
        urls = [cached_data]

    if not appconf["proxy"]["use_proxy"]: return Response("Forbidden", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

    headers = COMMON_HEADERS.copy()
    for k, v in request.headers.items():
        if k.lower() in ["range", "if-range", "x-playback-session-id"]:
            headers[k.lower()] = v

    client = await Network.get_async_client()
    probe_resp = None
    try:
        proxy_request = client.build_request("GET", urls[0], headers=headers, cookies=cookie_jar)
        probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
        
        proxy_resp = ProxyResponse(urls, headers, cookie_jar, status=probe_resp.status_code)
        if probe_resp.status_code < 400:
            for k, v in probe_resp.headers.items():
                if k.lower() in ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"]:
                    proxy_resp.headers[k] = v
        
        proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
        proxy_resp.headers["X-Accel-Buffering"] = "no"
        return proxy_resp
    except Exception as e:
        return Response(str(e), status=502)
    finally:
        if probe_resp: await probe_resp.aclose()


@proxy_bp.route("/proxy/<path:subpath>")
async def proxy_main(subpath):
    req_path = f"/proxy/{subpath}"

    if req_path.startswith("/proxy/video/") or req_path.startswith("/proxy/live/"):
        is_live = "/proxy/live/" in req_path
        try:
            if is_live:
                parts = req_path.removeprefix("/proxy/live/").split("?")[0].split("_")
                room_id = parts[0]
                vqn = parts[1] if len(parts) > 1 else "default"
                redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
                cached_data = await appredis.get(redis_key)
            else:
                parts = req_path.removeprefix("/proxy/video/").split("_")
                vid, vidx, vqn = parts[0], int(parts[1]), int(parts[2])
                cached_data = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")
        except ValueError:
            return Response("Bad Request", status=400)

        if not cached_data: return Response("Not Found", status=404)
        urls = json.loads(cached_data) if cached_data.startswith("[") else [cached_data]

        if not appconf["proxy"]["use_proxy"]: return Response("Forbidden", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}
        headers = COMMON_HEADERS.copy()
        for k, v in request.headers.items():
            if k.lower() in ["range", "if-range", "x-playback-session-id"]:
                headers[k.lower()] = v

        if is_live and ".m3u8" not in urls[0]:
            client_id = request.args.get("cid") or str(uuid.uuid4())
            stream, q = await live_manager.subscribe(urls[0], headers, cookie_jar, client_id)
            if not q: return Response("Upstream Error", status=502)

            async def generate_from_manager():
                disconnect_event = asyncio.Event()
                async def wait_for_disconnect():
                    ext = request.scope.get("extensions", {}).get("http.disconnect")
                    if ext:
                        try:
                            while True:
                                if (await ext())["type"] == "http.disconnect": break
                        except Exception: pass
                    disconnect_event.set()
                
                cleanup_task = asyncio.create_task(wait_for_disconnect())
                try:
                    while not disconnect_event.is_set():
                        try:
                            get_task = asyncio.create_task(q.get())
                            done, _ = await asyncio.wait([get_task, asyncio.create_task(disconnect_event.wait())], return_when=asyncio.FIRST_COMPLETED, timeout=15.0)
                            if disconnect_event.is_set() or not done:
                                if not done and stream.header_ready.is_set(): yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                                if disconnect_event.is_set(): break
                                continue
                            chunk = await get_task
                            if chunk is None: break
                            yield chunk
                        except Exception: break
                finally:
                    cleanup_task.cancel()
                    stream.remove_client(client_id)

            resp = Response(generate_from_manager(), status=stream.status_code or 200)
            resp.headers.update({"Connection": "keep-alive", "Keep-Alive": "timeout=86400", "Content-Type": "video/x-flv", "X-Accel-Buffering": "no"})
            return resp

        # DIRECT PROXY
        client = await Network.get_async_client()
        probe_resp = None
        try:
            proxy_request = client.build_request("GET", urls[0], headers=headers, cookies=cookie_jar)
            probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
            
            proxy_resp = ProxyResponse(urls, headers, cookie_jar, status=probe_resp.status_code)
            if is_live:
                proxy_resp.headers.update({"Connection": "keep-alive", "Keep-Alive": "timeout=86400", "Content-Type": "application/x-mpegURL" if ".m3u8" in urls[0] else "video/x-flv"})
            
            if probe_resp.status_code < 400:
                for k, v in probe_resp.headers.items():
                    if k.lower() in ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"]:
                        if is_live and k.lower() in ["content-type", "connection"]: continue
                        proxy_resp.headers[k] = v
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp
        except Exception as e:
            return Response(str(e), status=502)
        finally:
            if probe_resp: await probe_resp.aclose()

    elif req_path.startswith("/proxy/pic/"):
        return await render_proxy_pic(req_path)
    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST", "GET"])
async def proxy_live_disconnect():
    room_id, vqn, client_id = request.args.get("room_id"), request.args.get("vqn", "default"), request.args.get("cid")
    if not room_id or not client_id: return Response("Missing args", status=400)
    redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
    url = await appredis.get(redis_key)
    if url:
        url_str = url.decode() if isinstance(url, bytes) else url
        if url_str in live_manager.streams:
            live_manager.streams[url_str].remove_client(client_id, reason="Client Ping")
            return Response("OK", status=200)
    return Response("Not found", status=404)
