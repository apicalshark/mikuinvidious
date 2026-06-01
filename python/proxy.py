import asyncio
import uuid
from urllib.parse import urlparse

import httpx
from live_manager import live_manager
from quart import Blueprint, Response, request
from shared import (
    COMMON_HEADERS,
    Network,
    TicketManager,
    appconf,
    appredis,
    image_limiter,
)

proxy_bp = Blueprint("proxy", __name__)


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
            resp = await client.send(req, stream=True, follow_redirects=True)

            proxy_resp = ProxyResponse(
                resp, status=resp.status_code, url=url, headers=headers, client=client
            )
            # Add basic headers
            for k, v in resp.headers.items():
                if k.lower() in ["content-type", "content-length", "etag", "last-modified"]:
                    proxy_resp.headers[k] = v

            # Transfer ownership: clear resp so finally block doesn't close it
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
    Supports transparent retries for unstable upstream connections.
    """

    def __init__(
        self,
        upstream_resp,
        status=None,
        url=None,
        headers=None,
        cookies=None,
        client=None,
        *args,
        **kwargs,
    ):
        self.upstream_resp = upstream_resp
        self.url = url
        self.headers_template = headers
        self.cookies = cookies
        self.client = client

        # Create a generator that yields from the upstream response
        # and ensures it's closed when the generator is finished.
        async def response_generator():
            curr_resp = self.upstream_resp
            bytes_yielded = 0

            try:
                async for chunk in curr_resp.aiter_bytes(chunk_size=1024 * 64):
                    yield chunk
                    bytes_yielded += len(chunk)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ProtocolError) as e:
                # Log the error and exit the generator. This terminates the response
                # prematurely, allowing the browser/client to detect the truncation
                # and perform its own recovery (e.g., via Range requests).
                print(f"[Proxy] Upstream error after {bytes_yielded} bytes for {self.url[:60]}...: {repr(e)}")
                return
            except (asyncio.CancelledError, GeneratorExit):
                # Client disconnected, this is normal
                raise
            except Exception as e:
                # Unexpected errors
                print(f"[Proxy] Stream unexpected error after {bytes_yielded} bytes: {type(e).__name__}: {e}")
                return
            finally:
                try:
                    await curr_resp.aclose()
                except:
                    pass
                print(f"[Proxy] Upstream connection closed.")

        super().__init__(response_generator(), status=status, *args, **kwargs)
        self.headers["X-Content-Type-Options"] = "nosniff"
        self.headers["Access-Control-Allow-Origin"] = "*"
        self.headers["Access-Control-Expose-Headers"] = (
            "Content-Length, Content-Range, X-Miku-Proxy"
        )
        self.headers["X-Accel-Buffering"] = "no"

        # Avoid caching partial content or errors too aggressively
        if self.status_code == 200:
            self.headers["Cache-Control"] = "public, max-age=3600"
        elif self.status_code == 206:
            self.headers["Cache-Control"] = "no-cache"
        else:
            self.headers["Cache-Control"] = "no-store, must-revalidate"

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

    if isinstance(url, bytes):
        url = url.decode()
    urlp = urlparse(url)

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

    headers = COMMON_HEADERS.copy()

    # Add Bili-Ticket and dynamic session/trace IDs
    ticket = await TicketManager.get_ticket()
    if ticket:
        headers["x-bili-ticket"] = ticket

    headers["session_id"] = TicketManager._generate_session_id()
    headers["x-bili-trace-id"] = TicketManager._generate_trace_id()

    if appconf["credential"].get("buvid3"):
        headers["buvid"] = appconf["credential"]["buvid3"]
    if appconf["credential"].get("buvid4"):
        headers["buvid4"] = appconf["credential"]["buvid4"]

    # Forward headers from client (crucial for Range requests)
    for k, v in request.headers.items():
        if k.lower() in ["range", "if-range", "x-playback-session-id", "if-modified-since", "if-none-match"]:
            headers[k.lower()] = v

    client = await Network.get_async_client()
    resp = None
    try:
        proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
        resp = await client.send(proxy_request, stream=True, follow_redirects=True)

        proxy_resp = ProxyResponse(
            resp,
            status=resp.status_code,
            url=url,
            headers=headers,
            cookies=cookie_jar,
            client=client,
        )

        # Ensure cleanup if response is not started
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
                "cache-control",
            ]:
                proxy_resp.headers[k] = v

        # Only override Content-Type if it's generic or missing, and only for successful media responses
        if resp.status_code in [200, 206]:
            current_ct = proxy_resp.headers.get("Content-Type", "").lower()
            if not current_ct or "application/octet-stream" in current_ct:
                if media_type == "video":
                    proxy_resp.headers["Content-Type"] = "video/mp4"
                else:
                    proxy_resp.headers["Content-Type"] = "audio/mp4"

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
        try:
            if is_live:
                parts = req_path.removeprefix("/proxy/live/").split("?")[0].split("_")
                room_id = parts[0]
                vqn = parts[1] if len(parts) > 1 else "default"
                redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
                url = await appredis.get(redis_key)
                if not url and vqn == "default":
                    fallback_keys = await appredis.keys(f"miku_live_{room_id}_*")
                    if fallback_keys:
                        url = await appredis.get(fallback_keys[0])
            else:
                path_to_split = req_path.removeprefix("/proxy/video/")
                if "." in path_to_split:
                    path_to_split = path_to_split.rsplit(".", 1)[0]
                vid, vidx, vqn = path_to_split.split("_")
                url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")
        except ValueError:
            return Response("Bad Request", status=400)

        if not url:
            return Response("Not Found", status=404)

        if isinstance(url, bytes):
            url = url.decode()
        urlp = urlparse(url)

        if not appconf["proxy"]["use_proxy"]:
            return Response("Forbidden: Proxying is disabled.", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

        headers = COMMON_HEADERS.copy()

        # Add Bili-Ticket and dynamic session/trace IDs
        ticket = await TicketManager.get_ticket()
        if ticket:
            headers["x-bili-ticket"] = ticket

        headers["session_id"] = TicketManager._generate_session_id()
        headers["x-bili-trace-id"] = TicketManager._generate_trace_id()

        if appconf["credential"].get("buvid3"):
            headers["buvid"] = appconf["credential"]["buvid3"]
        if appconf["credential"].get("buvid4"):
            headers["buvid4"] = appconf["credential"]["buvid4"]

        # Forward headers from client
        for k, v in request.headers.items():
            if k.lower() in ["range", "if-range", "x-playback-session-id"]:
                headers[k.lower()] = v

        # SPECIAL HANDLING FOR LIVE FLV (Muxing/Multiplexing via LiveManager)
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
                            # Use a reasonable timeout (15s) for keep-alive
                            chunk = await asyncio.wait_for(q.get(), timeout=15.0)
                            if chunk is None:
                                break
                            yield chunk
                        except asyncio.TimeoutError:
                            # Send a minimal valid FLV Script Data tag if we have already sent headers
                            if stream.header_ready.is_set():
                                # Type 18, Size 0, Time 0, PrevSize 11
                                yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                            else:
                                # Still waiting for upstream? Send nothing to avoid corruption
                                continue
                except (asyncio.CancelledError, GeneratorExit):
                    reason = "Client disconnected"
                    raise
                except Exception as e:
                    reason = f"Generator error: {e}"
                finally:
                    stream.remove_client(client_id, reason=reason)

            proxy_resp = Response(generate_from_manager(), status=stream.status_code or 200)
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["Content-Type"] = "video/x-flv"
            proxy_resp.headers["X-Miku-Proxy"] = "LiveManager"
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp

        # DIRECT PROXY FOR VOD AND HLS LIVE
        client = await Network.get_async_client()
        resp = None

        try:
            # Send the request and get the response stream
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True, follow_redirects=True)
            print(f"[Proxy] Started direct stream: {url[:50]}... Status: {resp.status_code}")

            proxy_resp = ProxyResponse(
                resp,
                status=resp.status_code,
                url=url,
                headers=headers,
                cookies=cookie_jar,
                client=client,
            )

            # Ensure cleanup if response is not started
            try:
                proxy_resp.call_on_close(resp.aclose)
            except AttributeError:
                pass

            if request.args.get("dl") == "1" and not is_live:
                # Ensure filename is safe (alphanumeric + underscores)
                safe_vid = "".join(c for c in vid if c.isalnum() or c == "_")
                proxy_resp.headers["Content-Disposition"] = f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'

            # Set appropriate content type headers for live streams
            if is_live:
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
                    "cache-control",
                ]:
                    if is_live and k_lower in ["content-type", "connection"]:
                        continue
                    proxy_resp.headers[k] = v

            # Only override Content-Type if it's generic or missing
            if resp.status_code in [200, 206] and not is_live:
                current_ct = proxy_resp.headers.get("Content-Type", "").lower()
                if not current_ct or "application/octet-stream" in current_ct:
                    # Guess based on extension or default to video/mp4 for VOD
                    if ".mp4" in url.lower():
                        proxy_resp.headers["Content-Type"] = "video/mp4"
                    elif ".flv" in url.lower():
                        proxy_resp.headers["Content-Type"] = "video/x-flv"

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
