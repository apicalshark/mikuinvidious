import asyncio
import uuid

import httpx
from live_manager import live_manager
from proxy_utils import get_cookie_jar, get_forwarded_headers, get_target_url
from quart import Blueprint, Response, request
from shared import Network, appconf, appredis, image_limiter

proxy_bp = Blueprint("proxy", __name__)


async def render_proxy_pic(req_path):
    async with image_limiter:
        req_path = req_path[11:]
        domain = req_path.split("/")[0]

        if not (domain.endswith(".hdslb.com") or domain.endswith(".biliimg.com")):
            return Response("Forbidden", status=403)

        headers = get_forwarded_headers(request.headers)
        url = f"https://{req_path}"

        client = await Network.get_async_client()
        resp = None
        try:
            req = client.build_request("GET", url, headers=headers)
            resp = await client.send(req, stream=True, follow_redirects=True)

            proxy_resp = ProxyResponse(resp, status=resp.status_code)
            # Add basic headers
            for k, v in resp.headers.items():
                if k.lower() in ["content-type", "content-length", "etag", "last-modified"]:
                    proxy_resp.headers[k] = v

            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["X-Accel-Buffering"] = "no"

            # Transfer ownership to ProxyResponse. Set resp to None
            # to prevent the finally block from closing it.
            response_to_return = proxy_resp
            resp = None
            return response_to_return
        except Exception as e:
            print(f"[Proxy] Error in render_proxy_pic for {url}: {e}")
            return Response(str(e), status=502)
        finally:
            if resp:
                await resp.aclose()


class ProxyResponse(Response):
    """
    A specialized Response class that manages the lifetime of an upstream httpx response.
    Ensures that aclose() is called when the response is finished or closed.
    """

    def __init__(self, upstream_resp, *args, **kwargs):
        self.upstream_resp = upstream_resp

        async def response_generator():
            try:
                async for chunk in self.upstream_resp.aiter_bytes(chunk_size=1024 * 64):
                    yield chunk
            except httpx.RemoteProtocolError as e:
                print(f"[Proxy] Upstream connection dropped prematurely: {e}")
            except Exception as e:
                print(f"[Proxy] Stream error: {e}")
                raise
            finally:
                await self.upstream_resp.aclose()

        super().__init__(response_generator(), *args, **kwargs)

    async def aclose(self):
        """Called by Quart when the response is finished or the client disconnects."""
        await super().aclose()
        if self.upstream_resp:
            await self.upstream_resp.aclose()


@proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
async def proxy_dash(vid, idx, media_type, qn, cid):
    url = await appredis.get(f"miku_dash_url_{vid}_{idx}_{media_type}_{qn}_{cid}")
    if not url:
        return Response("Not Found", status=404)

    if isinstance(url, (bytes, bytearray)):
        url = url.decode()

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    cookie_jar = get_cookie_jar()
    headers = get_forwarded_headers(request.headers)

    client = await Network.get_async_client()
    resp = None
    try:
        proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
        resp = await client.send(proxy_request, stream=True, follow_redirects=True)

        proxy_resp = ProxyResponse(resp, status=resp.status_code)

        try:
            proxy_resp.call_on_close(resp.aclose)
        except AttributeError:
            pass

        for k, v in resp.headers.items():
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
        proxy_resp.headers["X-Accel-Buffering"] = "no"
        return proxy_resp
    except Exception as e:
        print(f"[Proxy] Error in proxy_dash: {e}")
        if resp:
            await resp.aclose()
        return Response(str(e), status=502)


@proxy_bp.route("/proxy/<path:subpath>")
async def proxy_main(subpath):
    req_path = f"/proxy/{subpath}"

    if req_path.startswith("/proxy/video/") or req_path.startswith("/proxy/live/"):
        is_live = "/proxy/live/" in req_path
        url, info = await get_target_url(req_path)

        if not url:
            return Response("Not Found", status=404)

        if isinstance(url, (bytes, bytearray)):
            url = url.decode()

        if not appconf["proxy"]["use_proxy"]:
            return Response("Forbidden: Proxying is disabled.", status=403)

        cookie_jar = get_cookie_jar()
        headers = get_forwarded_headers(request.headers)

        if is_live and ".m3u8" not in url:
            client_id = request.args.get("cid") or str(uuid.uuid4())
            stream, q = await live_manager.subscribe(url, headers, cookie_jar, client_id)
            if not q:
                return Response("Upstream Error", status=502)

            async def generate_from_manager():
                reason = "Stream ended"
                try:
                    while True:
                        try:
                            chunk = await asyncio.wait_for(q.get(), timeout=15.0)
                            if chunk is None:
                                break
                            yield chunk
                        except asyncio.TimeoutError:
                            if stream.header_ready.is_set():
                                yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                            else:
                                continue
                except (asyncio.CancelledError, GeneratorExit):
                    reason = "Client disconnected"
                    raise
                except Exception as e:
                    reason = f"Generator error: {e}"
                finally:
                    stream.remove_client(client_id, reason=reason)

            proxy_resp = Response(generate_from_manager(), status=stream.status_code or 200)
            proxy_resp.headers["Connection"] = "keep-alive"
            proxy_resp.headers["Keep-Alive"] = "timeout=10800"
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["Content-Type"] = "video/x-flv"
            proxy_resp.headers["X-Miku-Proxy"] = "LiveManager"
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp

        client = await Network.get_async_client()
        resp = None

        try:
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True, follow_redirects=True)

            proxy_resp = ProxyResponse(resp, status=resp.status_code)

            try:
                proxy_resp.call_on_close(resp.aclose)
            except AttributeError:
                pass

            if request.args.get("dl") == "1" and not is_live:
                vid, vidx, vqn = info
                safe_vid = "".join(c for c in vid if c.isalnum() or c == "_")
                proxy_resp.headers["Content-Disposition"] = f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'

            if is_live:
                proxy_resp.headers["Connection"] = "keep-alive"
                proxy_resp.headers["Keep-Alive"] = "timeout=10800"
                proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
                if ".m3u8" in url:
                    proxy_resp.headers["Content-Type"] = "application/x-mpegURL"
                else:
                    proxy_resp.headers["Content-Type"] = "video/x-flv"

            for k, v in resp.headers.items():
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
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp
        except Exception as e:
            print(f"[Proxy] Error proxying {url}: {e}")
            if resp:
                await resp.aclose()
            return Response(str(e), status=502)

    elif req_path.startswith("/proxy/pic/"):
        return await render_proxy_pic(req_path)
    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST", "GET"])
async def proxy_live_disconnect():
    room_id = request.args.get("room_id")
    vqn = request.args.get("vqn", "default")
    client_id = request.args.get("cid")

    if not room_id or not client_id:
        return Response("Missing room_id or cid", status=400)

    redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
    url = await appredis.get(redis_key)

    if url:
        if isinstance(url, (bytes, bytearray)):
            url = url.decode()
        if url in live_manager.streams:
            live_manager.streams[url].remove_client(client_id, reason="Client Ping")
            return Response("OK", status=200)

    return Response("Stream not found", status=404)
