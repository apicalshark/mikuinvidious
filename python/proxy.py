import asyncio
import ipaddress
import uuid
from urllib.parse import urlparse

from live_manager import live_manager
from quart import Blueprint, Response, abort, request
from stream import CdnProtocolError, CdnTimeoutError
from rate_limit import RATE_LIMITS, rate_limit
from shared import (
    Network,
    TicketManager,
    appconf,
    appredis,
    get_common_headers,
    image_limiter,
)
from stream import CdnConnection

proxy_bp = Blueprint("proxy", __name__)


async def is_safe_proxy_url(url: str) -> bool:
    """Validate that a URL is safe to proxy (not pointing to internal/private IPs)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        # Allow only specific Bilibili CDN domains
        allowed_domains = [
            ".hdslb.com",
            ".biliimg.com",
            ".bilivideo.com",
            ".bilivideo.cn",
            ".bilibili.com",
            ".acgvideo.com",
            ".akamaized.net",
        ]

        if not any(hostname == d.lstrip(".") or hostname.endswith(d) for d in allowed_domains):
            return False

        # Resolve and check IP
        # Resolve and check IP (both IPv4 and IPv6)
        import socket
        try:
            addr_infos = await asyncio.to_thread(
                socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            for family, _, _, _, sockaddr in addr_infos:
                ip = sockaddr[0]
                ip_obj = ipaddress.ip_address(ip)
                if (ip_obj.is_private or ip_obj.is_loopback or
                    ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved):
                    return False
        except socket.gaierror:
            return False

        return True
    except Exception:
        return False


async def render_proxy_pic(req_path):
    async with image_limiter:
        req_path = req_path[11:]

        if ".." in req_path or req_path.startswith("/"):
            return Response("Forbidden", status=403)

        url = f"https://{req_path}"

        if not await is_safe_proxy_url(url):
            return Response("Forbidden", status=403)

        headers = get_common_headers(appconf["bili"]).copy()

        client = await Network.get_async_client()
        try:
            req = client.build_request("GET", url, headers=headers)
            resp = await client.send(req, follow_redirects=True)

            content_type = resp.headers.get("content-type", "").lower()
            allowed_image_types = [
                "image/jpeg", "image/jpg", "image/png", "image/gif",
                "image/webp", "image/bmp", "image/avif"
            ]
            if not any(content_type.startswith(t) for t in allowed_image_types):
                print(f"[Proxy] Invalid Content-Type for image: {content_type}")
                return Response("Forbidden: Invalid content type", status=403)

            proxy_resp = Response(
                resp.content,
                status=resp.status_code,
                content_type=content_type,
            )
            proxy_resp.headers["Cache-Control"] = "public, max-age=86400"
            return proxy_resp
        except Exception as e:
            print(f"[Proxy] Error in render_proxy_pic for {url}: {e}")
            return Response("Upstream error", status=502)





@proxy_bp.route("/proxy/<path:subpath>")
@rate_limit(**RATE_LIMITS["proxy"])
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

        if not await is_safe_proxy_url(url):
            return Response("Forbidden: Invalid proxy target", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

        headers = get_common_headers(appconf["bili"]).copy()

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

        # DIRECT PROXY FOR VOD AND HLS LIVE (via raw socket CdnConnection)
        if cookie_jar:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
            existing = headers.get("cookie", "")
            headers["cookie"] = f"{existing}; {cookie_str}".lstrip("; ") if existing else cookie_str

        proxy_url = Network.get_proxy()
        conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)

        try:
            await conn.connect()
            await conn.send_request()
            resp_headers = await conn.read_response_headers()
            print(f"[Proxy] Connected: {url[:50]}... Status: {resp_headers.status_code}")

            if resp_headers.status_code in [403, 412, 514]:
                # Read response body for debugging
                try:
                    debug_body = await conn.read_debug_body()
                    print(f"[Proxy] CDN {resp_headers.status_code} body: {debug_body[:500]}")
                except Exception:
                    pass
                print(f"[Proxy] CDN returned {resp_headers.status_code}. Refreshing ticket and retrying...")
                await conn.close()
                ticket = await TicketManager.get_ticket(force_refresh=True)
                if ticket:
                    headers["x-bili-ticket"] = ticket
                else:
                    headers.pop("x-bili-ticket", None)
                headers["session_id"] = TicketManager._generate_session_id()
                headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
                conn = CdnConnection(url, headers=headers, proxy_url=proxy_url)
                await conn.connect()
                await conn.send_request()
                resp_headers = await conn.read_response_headers()
                print(f"[Proxy] Retry status: {resp_headers.status_code}")

            # If still 403 after retry, try backup URL, then lower qualities, then expire cache
            if resp_headers.status_code in [403, 412, 514] and not is_live:
                await conn.close()

                async def try_url(try_url: str, label: str) -> tuple:
                    c = CdnConnection(try_url, headers=headers, proxy_url=proxy_url)
                    try:
                        await c.connect()
                        await c.send_request()
                        rh = await c.read_response_headers()
                        if rh.status_code not in [403, 412, 514]:
                            print(f"[Proxy] {label} succeeded ({rh.status_code})")
                            return c, rh
                        await c.close()
                    except Exception as e:
                        print(f"[Proxy] {label} failed: {e}")
                        await c.close()
                    return None, None

                # CDN URL is stale — delete it so the backend re-fetches
                await appredis.delete(f"mikuinv_{vid}_{vidx}_{vqn}")
                print(f"[Proxy] Deleted stale CDN URL for qn={vqn}")

                # 1) Try backup URL for current quality
                bak_key = f"mikuinv_{vid}_{vidx}_{vqn}_bak"
                bak_url = await appredis.get(bak_key)
                if bak_url:
                    bak_url = bak_url.decode() if isinstance(bak_url, bytes) else bak_url
                    bak_conn, bak_rh = await try_url(bak_url, f"backup qn={vqn}")
                    if bak_conn:
                        conn, resp_headers = bak_conn, bak_rh
                    else:
                        await appredis.delete(bak_key)

                # 2) If backup also failed, try lower quality URLs from Redis
                if resp_headers.status_code in [403, 412, 514]:
                    print(f"[Proxy] Backup also failed, trying lower qualities...")
                    QUALITY_ORDER = [64, 32, 16]
                    try:
                        current_qn = int(vqn)
                    except (ValueError, TypeError):
                        print(f"[Proxy] Bad vqn: {vqn!r}")
                        return Response("Bad quality parameter", status=502)
                    fallback_url = None
                    for fq in QUALITY_ORDER:
                        if fq >= current_qn:
                            continue
                        fu = await appredis.get(f"mikuinv_{vid}_{vidx}_{fq}")
                        if not fu:
                            continue
                        fu = fu.decode() if isinstance(fu, bytes) else fu
                        fq_conn, fq_rh = await try_url(fu, f"qn={fq} primary")
                        if fq_conn:
                            conn, resp_headers = fq_conn, fq_rh
                            fallback_url = fu
                            break
                        # Try backup URL for this quality
                        fq_bak = await appredis.get(f"mikuinv_{vid}_{vidx}_{fq}_bak")
                        if fq_bak:
                            fq_bak = fq_bak.decode() if isinstance(fq_bak, bytes) else fq_bak
                            fq_conn2, fq_rh2 = await try_url(fq_bak, f"qn={fq} backup")
                            if fq_conn2:
                                conn, resp_headers = fq_conn2, fq_rh2
                                fallback_url = fq_bak
                                break
                    if not fallback_url:
                        print(f"[Proxy] All quality fallbacks failed for {vid}_{vidx}")
                        return Response("Upstream returned 403 and no fallback available", status=502)

            async def generate():
                try:
                    async for chunk in conn.iter_chunks():
                        yield chunk
                except (CdnProtocolError, CdnTimeoutError):
                    pass
                finally:
                    await conn.close()

            proxy_resp = Response(
                generate(),
                status=resp_headers.status_code,
            )
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["X-Accel-Buffering"] = "no"

            if request.args.get("dl") == "1" and not is_live:
                safe_vid = "".join(c for c in vid if c.isalnum() or c == "_")
                proxy_resp.headers["Content-Disposition"] = f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'

            if is_live:
                if ".m3u8" in url:
                    proxy_resp.headers["Content-Type"] = "application/x-mpegURL"
                else:
                    proxy_resp.headers["Content-Type"] = "video/x-flv"

            for k, v in resp_headers.headers.items():
                if k in [
                    "content-type",
                    "content-length",
                    "content-range",
                    "accept-ranges",
                    "etag",
                    "last-modified",
                    "cache-control",
                ]:
                    if is_live and k in ["content-type", "connection"]:
                        continue
                    proxy_resp.headers[k] = v

            if resp_headers.status_code in [200, 206] and not is_live:
                current_ct = proxy_resp.headers.get("Content-Type", "").lower()
                if not current_ct or "application/octet-stream" in current_ct:
                    if ".mp4" in url.lower():
                        proxy_resp.headers["Content-Type"] = "video/mp4"
                    elif ".flv" in url.lower():
                        proxy_resp.headers["Content-Type"] = "video/x-flv"

            return proxy_resp
        except Exception as e:
            print(f"[Proxy] Error proxying {url}: {e}")
            await conn.close()
            return Response("Upstream error", status=502)

    elif req_path.startswith("/proxy/pic/"):
        return await render_proxy_pic(req_path)
    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST"])
@rate_limit(**RATE_LIMITS["strict"])
async def proxy_live_disconnect():
    """Manual disconnect ping for live streams to clean up resources immediately."""
    from csrf import validate_csrf_token

    token = request.headers.get("X-CSRF-Token") or request.args.get("csrf_token")
    if not await validate_csrf_token(token):
        abort(403, description="CSRF token validation failed")

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
