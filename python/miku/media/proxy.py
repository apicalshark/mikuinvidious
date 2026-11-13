import asyncio
import re
import uuid
import weakref
from urllib.parse import urljoin

from quart import Blueprint, Response, has_request_context, request

from miku import state as shared
from miku.api.live import ScreenResolution
from miku.core.dns_cache import is_safe_public_url
from miku.core.ip_slots import PerIpSlotLimiter
from miku.core.rate_limit import RATE_LIMITS, get_client_ip, rate_limit
from miku.media.image_cache import image_cache
from miku.media.live_manager import live_manager
from miku.media.stream import CdnConnection, CdnProtocolError, CdnTimeoutError
from miku.services.credential import apply_cdn_cred_headers, get_web_api_cookies
from miku.state import (
    UPSTREAM_RESPONSE_BLACKLIST,
    Network,
    TicketManager,
    appconf,
    appredis,
    get_common_headers,
    image_limiter,
)

proxy_bp = Blueprint("proxy", __name__)

MAX_IMAGE_SIZE = 15 * 1024 * 1024  # 15 MB maximum proxy image size
PROXY_READ_TIMEOUT = 30.0
_discard_close_tasks: set[asyncio.Task] = set()


async def _quiet_close_conn(conn) -> None:
    try:
        await conn.close()
    except Exception:
        pass


def _schedule_discarded_conn_close(loop: asyncio.AbstractEventLoop, conn) -> None:
    if conn is None:
        return
    if loop.is_closed():
        writer = getattr(conn, "writer", None)
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        task = loop.create_task(_quiet_close_conn(conn))
        _discard_close_tasks.add(task)
        task.add_done_callback(_discard_close_tasks.discard)
    else:
        try:
            loop.call_soon_threadsafe(_schedule_discarded_conn_close, loop, conn)
        except RuntimeError:
            pass


async def _cdn_handshake(target_url: str, headers: dict, proxy_url: str | None, label: str):
    """Connect + send request + read headers; (conn, headers) or (None, None)."""
    c = CdnConnection(target_url, headers=headers, proxy_url=proxy_url, read_timeout=PROXY_READ_TIMEOUT)
    try:
        await c.connect()
        await c.send_request()
        rh = await c.read_response_headers()
        return c, rh
    except Exception as e:
        print(f"[Proxy] {label} handshake failed: {e}")
        await c.close()
        return None, None


async def _establish_cdn_connection(url: str, headers: dict, proxy_url: str | None):
    """Handshake with retries: 3 attempts with backoff. (conn, headers) or (None, None).

    A single WARP/SOCKS blip must not 502 the request.
    """
    for attempt in range(3):
        conn, resp_headers = await _cdn_handshake(url, headers, proxy_url, f"primary attempt {attempt + 1}/3")
        if conn is not None:
            return conn, resp_headers
        await asyncio.sleep(0.5 * (attempt + 1))
    return None, None


async def _try_backup_url(vid, vidx, vqn, headers: dict, proxy_url: str | None):
    """Try the cached backup CDN URL; (conn, headers) or (None, None)."""
    bak_url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}_bak")
    if not bak_url:
        return None, None
    if isinstance(bak_url, bytes):
        bak_url = bak_url.decode()
    if not await is_safe_proxy_url(bak_url):
        return None, None
    return await _establish_cdn_connection(bak_url, headers, proxy_url)


_ALLOWED_PROXY_DOMAINS = (
    ".hdslb.com",
    ".biliimg.com",
    ".bilivideo.com",
    ".bilivideo.cn",
    ".bilibili.com",
    ".acgvideo.com",
    ".akamaized.net",
)


async def is_safe_proxy_url(url: str) -> bool:
    """Validate that a URL is safe to proxy (not pointing to internal/private IPs)."""
    return await is_safe_public_url(url, _ALLOWED_PROXY_DOMAINS, use_dns_cache=False)


async def render_proxy_pic(req_path):
    req_path = req_path[11:]

    if ".." in req_path or req_path.startswith("/"):
        return Response("Forbidden", status=403)

    url = f"https://{req_path}"
    if has_request_context() and request.query_string and "?" not in req_path:
        qs = request.query_string.decode("latin-1")
        url = f"{url}?{qs}"
    if any(ch in url for ch in ("\r", "\n", "\x00")):
        return Response("Forbidden", status=403)

    if not await is_safe_proxy_url(url):
        return Response("Forbidden", status=403)

    # 1. Check local disk cache (fast path, bypasses semaphore and network)
    cached = await image_cache.get(url)
    if cached is not None:
        content, content_type = cached
        proxy_resp = Response(content, status=200, content_type=content_type)
        proxy_resp.headers["Cache-Control"] = "public, max-age=86400"
        proxy_resp.headers["X-Cache"] = "HIT"
        return proxy_resp

    async with image_limiter:
        # Re-check cache in case another coroutine fetched it while waiting for semaphore
        cached = await image_cache.get(url)
        if cached is not None:
            content, content_type = cached
            proxy_resp = Response(content, status=200, content_type=content_type)
            proxy_resp.headers["Cache-Control"] = "public, max-age=86400"
            proxy_resp.headers["X-Cache"] = "HIT"
            return proxy_resp

        headers = get_common_headers(appconf["bili"]).copy()

        client = await Network.get_image_client()
        resp = None
        current_url = url
        redirect_count = 0
        MAX_REDIRECTS = 3
        try:
            while True:
                req = client.build_request("GET", current_url, headers=headers)
                # Headers are validated before reading body
                resp = await client.send(req, stream=True, follow_redirects=False)

                if resp.is_redirect and "location" in resp.headers:
                    if redirect_count >= MAX_REDIRECTS:
                        await resp.aclose()
                        return Response("Forbidden: Too many redirects", status=403)
                    redirect_count += 1
                    loc = resp.headers["location"]
                    await resp.aclose()
                    next_url = urljoin(current_url, loc)
                    if not await is_safe_proxy_url(next_url):
                        print(f"[Proxy] Forbidden redirect destination: {next_url}")
                        return Response("Forbidden: Invalid redirect target", status=403)
                    current_url = next_url
                    continue
                break

            content_type = resp.headers.get("content-type", "").lower().split(";")[0].strip()
            # Bilibili's image CDN sometimes returns a bare extension
            # ("jpg") instead of a MIME type. Normalize those so covers
            # load; anything else non-image is still rejected below.
            bare_image_types = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "gif": "image/gif",
                "webp": "image/webp",
                "bmp": "image/bmp",
                "avif": "image/avif",
            }
            if content_type in bare_image_types:
                content_type = bare_image_types[content_type]
            allowed_image_types = {
                "image/jpeg",
                "image/jpg",
                "image/png",
                "image/gif",
                "image/webp",
                "image/bmp",
                "image/avif",
            }
            if content_type not in allowed_image_types:
                print(f"[Proxy] Invalid Content-Type for image: {content_type}")
                await resp.aclose()
                return Response("Forbidden: Invalid content type", status=403)

            # Reject oversized payloads early if declared
            cl_header = resp.headers.get("content-length")
            if cl_header and cl_header.isdigit() and int(cl_header) > MAX_IMAGE_SIZE:
                await resp.aclose()
                return Response("Payload Too Large", status=413)

            # Read the image content into memory and immediately close the upstream
            # connection, returning it to the HTTPX connection pool while still inside
            # the image_limiter semaphore. Streaming the response to slow client
            # browsers held pool connections open indefinitely, causing PoolTimeout (502)
            # when concurrent client requests exceeded max_connections.
            chunks = []
            total_size = 0
            try:
                async for chunk in resp.aiter_bytes(65536):
                    total_size += len(chunk)
                    if total_size > MAX_IMAGE_SIZE:
                        return Response("Payload Too Large", status=413)
                    chunks.append(chunk)
            finally:
                await resp.aclose()

            content = b"".join(chunks)

            # Store to disk cache asynchronously
            if resp.status_code == 200:
                await image_cache.set(url, content, content_type)

            proxy_resp = Response(
                content,
                status=resp.status_code,
                content_type=content_type,
            )
            proxy_resp.headers["Cache-Control"] = "public, max-age=86400"
            proxy_resp.headers["X-Cache"] = "MISS"
            return proxy_resp
        except Exception as e:
            if resp:
                await resp.aclose()
            err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            print(f"[Proxy] Error in render_proxy_pic for {url}: {err}")
            return Response("Upstream error", status=502)


async def _image_rate_key(req) -> str:
    """One image bucket per client IP.

    The default key is ``ip:path``, which gives every distinct cover URL its
    own allowance — a gallery flood across unique URLs never hits a limit and
    each image costs a socket. Keying on the route caps images per IP.
    """
    return f"{get_client_ip(req)}:image"


@proxy_bp.route("/proxy/pic/<path:subpath>")
@rate_limit(**RATE_LIMITS["loose"], key_func=_image_rate_key)
async def proxy_pic(subpath):
    return await render_proxy_pic(f"/proxy/pic/{subpath}")


async def _proxy_ip_key(req) -> str:
    """One coarse bucket per client IP across every ``/proxy/<path>`` URL.

    The default key is ``ip:path``; every vid/quality/room is its own path,
    so a crawler walking renditions got a fresh allowance per URL and the
    limit never bound. The per-path limit stays (it throttles hammering one
    URL); this one caps the IP as a whole.
    """
    return f"{get_client_ip(req)}:proxy"


# Concurrently open streams per client IP — strictly per-IP, no global
# ceiling (see ip_slots.py). A browser tab plays one progressive stream
# (two briefly while a seek replaces the old request); HLS playlist polls
# are short-lived. Live FLV holds one long connection per watched room.
PROXY_MAX_DIRECT_STREAMS_PER_IP = 6
PROXY_MAX_LIVE_STREAMS_PER_IP = 4
direct_stream_slots = PerIpSlotLimiter(PROXY_MAX_DIRECT_STREAMS_PER_IP)
live_stream_slots = PerIpSlotLimiter(PROXY_MAX_LIVE_STREAMS_PER_IP)

# /proxy/video/<vid>_<idx>_<qn>[.ext] — vid is a BV/av id, or an audio
# au/am id (bare digits too: views_audio keys raw route params).
_PROGRESSIVE_PATH_RE = re.compile(
    r"^(BV[A-Za-z0-9]{10}|(?:av|au|am)?\d{1,20})_(\d{1,4})_(\d{1,6})(?:\.[A-Za-z0-9]{1,5})?$"
)
# /proxy/live/<room_id>[_<qn>] — digits only. room_id used to reach an
# appredis.keys() glob unvalidated ("*" scanned the whole keyspace).
_LIVE_PATH_RE = re.compile(r"^(\d{1,20})(?:_(\d{1,6}))?$")
# Exactly one byte-range: ``bytes=N-``, ``bytes=N-M`` or suffix ``bytes=-N``.
# Multi-range lists are the CVE-2011-3192 ("Apache Killer") amplification
# shape and no player sends them. Open-ended ranges stay legal: native
# <video> opens progressive files with ``bytes=0-``.
_SINGLE_RANGE_RE = re.compile(r"^bytes=(?:(\d{1,19})-(\d{0,19})|-(\d{1,19}))$")


def _is_acceptable_progressive_range(raw: str | None) -> bool:
    raw = (raw or "").strip()
    if not raw:
        return True
    m = _SINGLE_RANGE_RE.match(raw)
    if not m:
        return False
    start, end = m.group(1), m.group(2)
    return not (start is not None and end and int(end) < int(start))


def _too_many_streams() -> Response:
    return Response(
        "Too Many Requests: too many concurrent streams from this address",
        status=429,
        headers={"Retry-After": "1", "Access-Control-Allow-Origin": "*"},
    )


async def _first_live_quality_url(room_id: str):
    """Fallback lookup for ``/proxy/live/<room>`` without a quality.

    Bounded ``SCAN`` instead of ``KEYS``: KEYS is O(keyspace) and blocks
    Redis for every caller while it runs.
    """
    cursor = 0
    truncated = False
    for _ in range(8):
        cursor, keys = await appredis.scan(cursor=cursor, match=f"miku_live_{room_id}_*", count=100)
        # Redis treats COUNT as a hint, so cap follow-up GETs explicitly.
        truncated |= len(keys) > 100
        for key in keys[:100]:
            url = await appredis.get(key)
            if url:
                return url
        if not cursor:
            break
    # SCAN may not reach a matching legacy key in a large database. Known
    # qualities can also be checked directly without traversing the keyspace.
    if cursor or truncated:
        for quality in ScreenResolution:
            url = await appredis.get(f"miku_live_{room_id}_{quality.value}")
            if url:
                return url
    return None


@proxy_bp.route("/proxy/<path:subpath>")
@rate_limit(limit=300, window=60, key_func=_proxy_ip_key)
@rate_limit(**RATE_LIMITS["proxy"])
async def proxy_main(subpath):
    req_path = f"/proxy/{subpath}"

    if req_path.startswith("/proxy/video/") or req_path.startswith("/proxy/live/"):
        is_live = "/proxy/live/" in req_path
        if is_live:
            m = _LIVE_PATH_RE.match(req_path.removeprefix("/proxy/live/"))
            if not m:
                return Response("Bad Request", status=400)
            room_id = m.group(1)
            vqn = m.group(2) or "default"
        else:
            m = _PROGRESSIVE_PATH_RE.match(req_path.removeprefix("/proxy/video/"))
            if not m:
                return Response("Bad Request", status=400)
            vid, vidx, vqn = m.group(1), m.group(2), m.group(3)
            if not _is_acceptable_progressive_range(request.headers.get("Range")):
                return Response(
                    "Range Not Satisfiable: a single byte range is required",
                    status=416,
                    headers={"Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*"},
                )

        if is_live:
            redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
            url = await appredis.get(redis_key)
            if not url and vqn == "default":
                url = await _first_live_quality_url(room_id)
        else:
            url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")

        if not url:
            return Response("Not Found", status=404)

        if isinstance(url, bytes):
            url = url.decode()

        if not await is_safe_proxy_url(url):
            return Response("Forbidden: Invalid proxy target", status=403)

        if is_live:
            headers = get_common_headers(appconf["bili"]).copy()
        else:
            # VOD progressive must use the web-Chrome CDN set, NOT the
            # Android app UA in get_common_headers(): Bilibili's CDN edge
            # (upos mirrors, akamaized, .bilivideo.com) 403s the BiliDroid
            # UA — same verified gotcha the DASH path already handles via
            # _build_dash_cdn_headers(). Sent the wrong UA, every durl-only
            # video 403s on primary+backup, keys get deleted, and all later
            # hits 404 (e.g. bangumi ep98604).
            from miku.core.fingerprints import get_cdn_headers

            headers = get_cdn_headers(referer=appconf["bili"].get("referer", "https://www.bilibili.com"))

        # Add Bili-Ticket, dynamic session/trace IDs, and active credentials
        ticket = await TicketManager.get_ticket()
        headers["session_id"] = TicketManager._generate_session_id()
        headers["x-bili-trace-id"] = TicketManager._generate_trace_id()
        apply_cdn_cred_headers(headers, shared.appcred, ticket)

        # Forward headers from client
        for k, v in request.headers.items():
            if k.lower() in ["range", "if-range", "x-playback-session-id"]:
                headers[k.lower()] = v

        # SPECIAL HANDLING FOR LIVE FLV (Muxing/Multiplexing via LiveManager)
        if is_live and ".m3u8" not in url:
            live_slot = live_stream_slots.try_acquire(get_client_ip(request))
            if live_slot is None:
                return _too_many_streams()
            client_id = request.args.get("cid") or str(uuid.uuid4())
            cookie_jar = get_web_api_cookies(shared.appcred)
            try:
                stream, q = await live_manager.subscribe(url, headers, cookie_jar, client_id)
            except BaseException:
                live_slot.release()
                raise
            if not q:
                live_slot.release()
                return Response("Upstream Error", status=502)

            started = False

            async def generate_from_manager():
                nonlocal started
                started = True
                reason = "Stream ended"
                try:
                    while True:
                        try:
                            # Use a reasonable timeout (15s) for keep-alive
                            async with asyncio.timeout(15.0):
                                chunk = await q.get()
                            if chunk is None:
                                break
                            yield chunk
                        except TimeoutError:
                            # Send a minimal valid FLV Script Data tag if we have already sent headers
                            if stream.header_ready.is_set():
                                # Type 18, Size 0, Time 0, PrevSize 11
                                yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                            else:
                                # Still waiting for upstream? Send nothing to avoid corruption
                                continue
                except asyncio.CancelledError, GeneratorExit:
                    reason = "Client disconnected"
                    raise
                except Exception as e:
                    reason = f"Generator error: {e}"
                finally:
                    stream.remove_client(client_id, reason=reason)
                    live_slot.release()

            live_gen = generate_from_manager()
            # A body that is never iterated never runs its finally: free the
            # slot (and the subscriber) when the generator is collected.
            weakref.finalize(live_gen, live_slot.release)

            def _discard_cleanup():
                if not started:
                    stream.remove_client(client_id, "Response discarded")

            weakref.finalize(live_gen, _discard_cleanup)
            proxy_resp = Response(live_gen, status=stream.status_code or 200)
            proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
            proxy_resp.headers["Content-Type"] = "video/x-flv"
            proxy_resp.headers["X-Miku-Proxy"] = "LiveManager"
            proxy_resp.headers["X-Accel-Buffering"] = "no"
            return proxy_resp

        # DIRECT PROXY FOR VOD AND HLS LIVE (via raw socket CdnConnection)
        slot = direct_stream_slots.try_acquire(get_client_ip(request))
        if slot is None:
            return _too_many_streams()
        handed_off = False
        try:
            proxy_url = Network.get_proxy()

            conn, resp_headers = await _establish_cdn_connection(url, headers, proxy_url)
            if conn is None and not is_live:
                # Primary unreachable (transport-level, not URL staleness — so the
                # backup key is kept) — try the backup URL before 502.
                conn, resp_headers = await _try_backup_url(vid, vidx, vqn, headers, proxy_url)
            if conn is None:
                return Response("Upstream error", status=502)

            try:
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
                    conn, resp_headers = await _cdn_handshake(url, headers, proxy_url, "ticket-refresh retry")
                    if conn is None:
                        return Response("Upstream error", status=502)
                    print(f"[Proxy] Retry status: {resp_headers.status_code}")

                # If still 403 after retry, try backup URL, then lower qualities, then expire cache
                if resp_headers.status_code in [403, 412, 514] and not is_live:
                    await conn.close()

                    async def try_url(target_url: str, label: str) -> tuple:
                        if not await is_safe_proxy_url(target_url):
                            print(f"[Proxy] {label} rejected unsafe fallback URL")
                            return None, None
                        c = CdnConnection(
                            target_url, headers=headers, proxy_url=proxy_url, read_timeout=PROXY_READ_TIMEOUT
                        )
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
                        print("[Proxy] Backup also failed, trying lower qualities...")
                        QUALITY_ORDER = [64, 32, 16]
                        try:
                            current_qn = int(vqn)
                        except ValueError, TypeError:
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

                gen_started = False

                async def generate():
                    nonlocal gen_started
                    gen_started = True
                    try:
                        async for chunk in conn.iter_chunks():
                            yield chunk
                    except asyncio.CancelledError, GeneratorExit:
                        # Client went away mid-stream: close upstream and let
                        # cancellation propagate (no error log — this is normal).
                        raise
                    except (CdnProtocolError, CdnTimeoutError, TimeoutError, OSError) as e:
                        # Mid-body cut: end the stream early WITHOUT raising only when
                        # downstream Content-Length was declared, so the short body
                        # surfaces as a length mismatch and the client/player can detect
                        # truncation. If Content-Length was omitted (unframed/chunked/close-delimited),
                        # propagate the read failure so the transfer does not appear complete.
                        cl = resp_headers.headers.get("content-length")
                        has_content_length = False
                        if cl is not None and str(cl).strip():
                            try:
                                has_content_length = int(str(cl).strip()) >= 0
                            except ValueError:
                                has_content_length = False
                        if not has_content_length:
                            raise
                        print(f"[Proxy] Mid-body cut for {url[:80]}...: {e}")
                        return
                    finally:
                        await conn.close()
                        slot.release()

                gen = generate()
                loop = asyncio.get_running_loop()

                def _on_gen_discard(c=conn, s=slot, lp=loop):
                    s.release()
                    if not gen_started:
                        _schedule_discarded_conn_close(lp, c)

                # Never-iterated bodies skip the finally: release and close on collection.
                weakref.finalize(gen, _on_gen_discard)
                handed_off = True
                proxy_resp = Response(
                    gen,
                    status=resp_headers.status_code,
                )
                proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
                proxy_resp.headers["X-Accel-Buffering"] = "no"

                if request.args.get("dl") == "1" and not is_live:
                    safe_vid = "".join(c for c in vid if c.isalnum() or c == "_")
                    proxy_resp.headers["Content-Disposition"] = (
                        f'attachment; filename="miku_{safe_vid}_p{vidx}_{vqn}.mp4"'
                    )

                if is_live:
                    if ".m3u8" in url:
                        proxy_resp.headers["Content-Type"] = "application/x-mpegURL"
                    else:
                        proxy_resp.headers["Content-Type"] = "video/x-flv"

                for k, v in resp_headers.headers.items():
                    if k.lower() in UPSTREAM_RESPONSE_BLACKLIST:
                        continue
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
        finally:
            if not handed_off:
                slot.release()

    else:
        return Response("I'm a teapot", status=418)


@proxy_bp.route("/proxy/live/disconnect", methods=["POST"])
@rate_limit(**RATE_LIMITS["strict"])
async def proxy_live_disconnect():
    """Manual disconnect ping for live streams to clean up resources immediately."""
    from miku.core.csrf import validate_csrf_token

    token = request.headers.get("X-CSRF-Token") or request.args.get("csrf_token")
    if not token and request.mimetype == "application/x-www-form-urlencoded":
        # Unload beacons (sendBeacon) can't set headers, so player.js puts
        # the token in a form-urlencoded body instead of the query string
        # (keeps per-session tokens out of access logs).
        token = (await request.form).get("csrf_token")
    if not await validate_csrf_token(token):
        return Response("CSRF token validation failed", status=403)

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
        stream = live_manager.streams.get(url)
        # Only short-circuit when this generation actually owns the cid;
        # otherwise fall through to the scan (a no-op remove would mask a
        # stale slot on a rotated generation as success).
        if stream is not None and client_id in stream.clients:
            stream.remove_client(client_id, reason="Client Ping")
            return Response("OK", status=200)

    # Fallback: the redis key is overwritten with a freshly-signed play URL
    # on every page load, so a delayed beacon can name a cid whose stream
    # generation no longer matches redis. cids are 128-bit random, hence
    # globally unique — scan for the owning stream directly instead of
    # leaving the stale slot to the grace-period reaper.
    for stream in list(live_manager.streams.values()):
        if client_id in stream.clients:
            stream.remove_client(client_id, reason="Client Ping (stale generation)")
            return Response("OK", status=200)

    return Response("Stream not found", status=404)
