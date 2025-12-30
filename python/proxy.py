import asyncio
import json
import uuid
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
        req_path = req_path[11:]
        domain = req_path.split("/")[0]

        if not (domain.endswith(".hdslb.com") or domain.endswith(".biliimg.com")):
            return Response("Forbidden", status=403)

        headers = COMMON_HEADERS.copy()
        url = f"https://{req_path}"

        client = await Network.get_async_client()
        resp = None
        try:
            req = client.build_request("GET", url, headers=headers)
            resp = await client.send(req, follow_redirects=True)
            return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("content-type"))
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


class ProxyResponse(Response):
    """
    A specialized Response class that manages upstream httpx response(s) with CDN fallback.
    """

    def __init__(self, urls, headers, cookies, *args, **kwargs):
        self.urls = urls if isinstance(urls, list) else [urls]
        self.client_headers = headers
        self.cookies = cookies
        self.upstream_resp = None
        self._current_url_index = 0

        async def response_generator():
            try:
                while self._current_url_index < len(self.urls):
                    url = self.urls[self._current_url_index]
                    client = await Network.get_async_client()

                    try:
                        proxy_request = client.build_request(
                            "GET", url, headers=self.client_headers, cookies=self.cookies
                        )
                        self.upstream_resp = await client.send(proxy_request, stream=True, follow_redirects=True)

                        # If we get a valid streamable status, proceed
                        if self.upstream_resp.status_code < 400:
                            print(f"[Proxy] Success with CDN {self._current_url_index}: {url[:50]}...")
                            try:
                                async for chunk in self.upstream_resp.aiter_bytes(chunk_size=1024 * 64):
                                    yield chunk
                                return  # Finished successfully
                            finally:
                                await self.upstream_resp.aclose()
                                self.upstream_resp = None
                        else:
                            print(
                                f"[Proxy] CDN {self._current_url_index} returned {self.upstream_resp.status_code}, trying next..."
                            )
                            await self.upstream_resp.aclose()
                            self.upstream_resp = None
                    except Exception as e:
                        print(f"[Proxy] CDN {self._current_url_index} failed with error: {e}")
                        if self.upstream_resp:
                            await self.upstream_resp.aclose()
                            self.upstream_resp = None

                    self._current_url_index += 1
                print("[Proxy] All CDNs exhausted.")
            except (asyncio.CancelledError, GeneratorExit):
                print("[Proxy] Generator cancelled/exit during streaming.")
                raise
            finally:
                if self.upstream_resp:
                    await self.upstream_resp.aclose()
                    self.upstream_resp = None

        super().__init__(ClosingIterator(response_generator(), lambda: self.upstream_resp), *args, **kwargs)

    async def aclose(self):
        """Explicitly close the body if it has an aclose method."""
        if hasattr(self.response, "aclose"):
            await self.response.aclose()
        await super().aclose()


@proxy_bp.route("/proxy/dash/<media_type>/<int:qn>")
async def proxy_dash(media_type, qn):
    cached_data = await appredis.get(f"miku_dash_url_{media_type}_{qn}")
    if not cached_data:
        return Response("Not Found", status=404)

    try:
        urls = json.loads(cached_data) if cached_data.startswith("[") else [cached_data]
    except Exception:
        urls = [cached_data]

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

    headers = COMMON_HEADERS.copy()

    # Forward headers from client
    for k, v in request.headers.items():
        k_lower = k.lower()
        if k_lower in ["range", "if-range", "x-playback-session-id"]:
            headers[k_lower] = v

    # Initial probe to get headers from the FIRST working CDN
    client = await Network.get_async_client()
    probe_resp = None
    working_urls = urls

    try:
        # We need a response to get headers, but ProxyResponse handles the stream.
        # So we do a quick stream=True call and then immediately wrap it or similar.
        # To keep it simple and robust, let's just use the first URL for headers
        # but in ProxyResponse we'll do the actual switching.

        proxy_request = client.build_request("GET", urls[0], headers=headers, cookies=cookie_jar)
        probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
        print(f"[Proxy-Dash] Probe response: {urls[0][:50]}... Status: {probe_resp.status_code}")

        try:
            proxy_resp = ProxyResponse(urls, headers, cookie_jar, status=probe_resp.status_code)

            if probe_resp.status_code < 400:
                for k, v in probe_resp.headers.items():
                    k_lower = k.lower()
                    if k_lower in [
                        "content-type",
                        "content-length",
                        "content-range",
                        "accept-ranges",
                        "etag",
                        "last-modified",
                    ]:
                        proxy_resp.headers[k] = v

            if media_type == "video":
                proxy_resp.headers["Content-Type"] = "video/mp4"
            else:
                proxy_resp.headers["Content-Type"] = "audio/mp4"

            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"

            return proxy_resp
        except Exception as e:
            print(f"[Proxy-Dash] Error: {e}")
            raise
        finally:
            if probe_resp:
                await probe_resp.aclose()

    except Exception as e:
        print(f"[Proxy] Error in proxy_dash probe: {e}")
        if probe_resp:
            await probe_resp.aclose()
        return Response(str(e), status=502)


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
                if not cached_data and vqn == "default":
                    fallback_keys = await appredis.keys(f"miku_live_{room_id}_*")
                    if fallback_keys:
                        cached_data = await appredis.get(fallback_keys[0])
            else:
                parts = req_path.removeprefix("/proxy/video/").split("_")
                vid = parts[0]
                vidx = int(parts[1])
                vqn = int(parts[2])
                cached_data = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")
        except ValueError:
            return Response("Bad Request", status=400)

        if not cached_data:
            return Response("Not Found", status=404)

        try:
            urls = json.loads(cached_data) if cached_data.startswith("[") else [cached_data]
        except Exception:
            urls = [cached_data]

        url = urls[0]  # Primary for LiveManager compatibility

        if not appconf["proxy"]["use_proxy"]:
            return Response("Forbidden: Proxying is disabled.", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

        headers = COMMON_HEADERS.copy()

        # Forward headers from client
        for k, v in request.headers.items():
            k_lower = k.lower()
            if k_lower in ["range", "if-range", "x-playback-session-id"]:
                headers[k_lower] = v

        # SPECIAL HANDLING FOR LIVE FLV (Muxing/Multiplexing via LiveManager)
        if is_live and ".m3u8" not in url:
            client_id = request.args.get("cid") or str(uuid.uuid4())
            stream, q = await live_manager.subscribe(url, headers, cookie_jar, client_id)
            if not q:
                return Response("Upstream Error", status=502)

            # Extra safety: listen for disconnect via ASGI scope if possible
            disconnect_event = asyncio.Event()

            async def wait_for_disconnect():
                disconnect_ext = request.scope.get("extensions", {}).get("http.disconnect")
                if not disconnect_ext:
                    return
                try:
                    while True:
                        message = await disconnect_ext()
                        if message["type"] == "http.disconnect":
                            break
                except Exception:
                    pass
                finally:
                    disconnect_event.set()

            async def generate_from_manager():
                reason = "Stream ended"
                # Start background disconnect listener
                cleanup_task = asyncio.create_task(wait_for_disconnect())
                try:
                    while not disconnect_event.is_set():
                        try:
                            # Wait for chunk OR disconnect
                            get_task = asyncio.create_task(q.get())
                            done, pending = await asyncio.wait(
                                [get_task, asyncio.create_task(disconnect_event.wait())],
                                return_when=asyncio.FIRST_COMPLETED,
                                timeout=15.0,
                            )

                            for p in pending:
                                p.cancel()

                            if disconnect_event.is_set():
                                reason = "Client disconnected (Event)"
                                break

                            if not done:
                                # Timeout hit, send keep-alive
                                if stream.header_ready.is_set():
                                    yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                                continue

                            chunk = await get_task
                            if chunk is None:
                                break
                            yield chunk
                        except Exception as e:
                            reason = f"Inner generator error: {e}"
                            break
                except (asyncio.CancelledError, GeneratorExit):
                    reason = "Client disconnected (Cancel/Exit)"
                    raise
                finally:
                    cleanup_task.cancel()
                    stream.remove_client(client_id, reason=reason)

            proxy_resp = Response(generate_from_manager(), status=stream.status_code or 200)
            proxy_resp.headers["Connection"] = "keep-alive"
            proxy_resp.headers["Keep-Alive"] = "timeout=10800"
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["Content-Type"] = "video/x-flv"
            proxy_resp.headers["X-Miku-Proxy"] = "LiveManager"
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp

        # DIRECT PROXY FOR VOD AND HLS LIVE
        client = await Network.get_async_client()
        probe_resp = None

        try:
            # Probe the first URL to get headers
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
            print(f"[Proxy] Probe response: {url[:50]}... Status: {probe_resp.status_code}")

            proxy_resp = ProxyResponse(urls, headers, cookie_jar, status=probe_resp.status_code)

            if request.args.get("dl") == "1" and not is_live:
                # Ensure filename is safe (alphanumeric + underscores)
                vid_val = vid if "vid" in locals() else "video"
                safe_vid = "".join(c for c in vid_val if c.isalnum() or c == "_")
                proxy_resp.headers["Content-Disposition"] = f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'

            # Set appropriate content type and connection headers for live streams (Step 3)
            if is_live:
                proxy_resp.headers["Connection"] = "keep-alive"
                proxy_resp.headers["Keep-Alive"] = "timeout=10800"
                proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
                if ".m3u8" in url:
                    proxy_resp.headers["Content-Type"] = "application/x-mpegURL"
                else:
                    proxy_resp.headers["Content-Type"] = "video/x-flv"

            if probe_resp.status_code < 400:
                for k, v in probe_resp.headers.items():
                    k_lower = k.lower()
                    if k_lower in [
                        "content-type",
                        "content-length",
                        "content-range",
                        "accept-ranges",
                        "etag",
                        "last-modified",
                    ]:
                        if is_live and k_lower in ["content-type", "connection"]:
                            continue
                        proxy_resp.headers[k] = v

            return proxy_resp
        except Exception as e:
            print(f"[Proxy] Error in proxy_main probe: {e}")
            return Response(str(e), status=502)
        finally:
            if probe_resp:
                await probe_resp.aclose()

    elif req_path.startswith("/proxy/pic/"):
        return await render_proxy_pic(req_path)
    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST", "GET"])
async def proxy_live_disconnect():
    """Manual disconnect ping for live streams to clean up resources immediately."""
    room_id = request.args.get("room_id")
    vqn = request.args.get("vqn", "default")
    client_id = request.args.get("cid")

    if not room_id or not client_id:
        return Response("Missing room_id or cid", status=400)

    redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
    url = await appredis.get(redis_key)

    if url:
        if isinstance(url, bytes):
            url = url.decode()
        if url in live_manager.streams:
            live_manager.streams[url].remove_client(client_id, reason="Client Ping")
            return Response("OK", status=200)

    return Response("Stream not found", status=404)
