import asyncio
import re
import uuid
from urllib.parse import urlparse

import httpx
import orjson
from live_manager import live_manager
from quart import Blueprint, Response, request
from shared import (
    COMMON_HEADERS,
    Network,
    TicketManager,
    appconf,
    appredis,
    build_cdn_media_headers,
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


def _parse_client_range_header(client_range):
    """
    Parse the browser Range request.
    Returns (start, end_inclusive_or_none, mode) with mode in full|open|bounded|none.
    """
    if not client_range:
        return 0, None, "none"

    match = re.match(r"bytes\s*=\s*(\d+)\s*-\s*(\d*)", client_range.strip(), re.IGNORECASE)
    if not match:
        return 0, None, "none"

    start = int(match.group(1))
    end_part = match.group(2)
    if end_part == "":
        return (0, None, "full") if start == 0 else (start, None, "open")
    return start, int(end_part), "bounded"


def _parse_stream_range(upstream_resp, headers_template):
    """Derive byte range for CDN stitching and the Content-Range header sent to the browser."""
    content_range = upstream_resp.headers.get("content-range")
    client_range = (headers_template or {}).get("range", "").strip()
    c_start, c_end, c_mode = _parse_client_range_header(client_range)

    start_byte = c_start
    end_byte = None
    total_size = None

    if content_range:
        match = re.match(r"bytes\s+(\d+)-(\d+)(?:/(\d+))?", content_range, re.IGNORECASE)
        if match:
            up_start = int(match.group(1))
            segment_end = int(match.group(2))
            if match.group(3):
                total_size = int(match.group(3))

            if c_mode == "bounded":
                start_byte = c_start
                end_byte = c_end
            elif c_mode == "open" and total_size:
                start_byte = c_start
                end_byte = total_size - 1
            elif c_mode in ("full", "none") and total_size:
                start_byte = 0 if c_mode == "full" else up_start
                end_byte = total_size - 1
            else:
                start_byte = up_start
                end_byte = segment_end
    elif upstream_resp.status_code == 200:
        content_length = upstream_resp.headers.get("content-length")
        if content_length:
            try:
                end_byte = int(content_length) - 1
                if c_mode == "bounded":
                    start_byte = c_start
                    end_byte = min(c_end, end_byte)
                elif c_mode == "open":
                    start_byte = c_start
            except ValueError:
                pass

    if c_mode == "bounded" and c_end is not None:
        start_byte = c_start
        end_byte = c_end

    return start_byte, end_byte, total_size


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
        fallback_urls=None,
        *args,
        **kwargs,
    ):
        self.upstream_resp = upstream_resp
        self.url = url
        self._url_candidates = list(
            dict.fromkeys([url] + [u for u in (fallback_urls or []) if u])
        )
        self._url_index = 0
        self.headers_template = headers
        self.cookies = cookies
        self.client = client
        self.stitch_start_byte, self.stitch_end_byte, self.stitch_total_size = _parse_stream_range(
            upstream_resp, headers
        )

        # Create a generator that yields from the upstream response
        # and ensures it's closed when the generator is finished.
        async def response_generator():
            curr_resp = self.upstream_resp
            bytes_yielded = 0
            start_byte = self.stitch_start_byte
            end_byte = self.stitch_end_byte

            max_retries = 12
            retry_count = 0
            total_reconnects = 0
            skip_leading = 0
            # Only reset retry_count after CDN serves a meaningful chunk; tiny drops keep backoff climbing.
            meaningful_chunk_bytes = 1024 * 1024
            bytes_since_retry_reset = 0

            try:
                while True:
                    try:
                        bytes_since_retry_reset = 0
                        async for chunk in curr_resp.aiter_bytes(chunk_size=1024 * 64):
                            if skip_leading:
                                if skip_leading >= len(chunk):
                                    skip_leading -= len(chunk)
                                    continue
                                chunk = chunk[skip_leading:]
                                skip_leading = 0
                            yield chunk
                            bytes_yielded += len(chunk)
                            bytes_since_retry_reset += len(chunk)
                            if bytes_since_retry_reset >= meaningful_chunk_bytes:
                                retry_count = 0
                                bytes_since_retry_reset = 0

                        # If we reached here, the generator finished normally.
                        # Check if we got all expected bytes (if end_byte is known).
                        if end_byte is not None:
                            expected_bytes = end_byte - start_byte + 1
                            if bytes_yielded < expected_bytes:
                                # Stream terminated early without exception, treat as protocol error/premature close
                                raise httpx.RemoteProtocolError(
                                    f"Connection closed early. Yielded {bytes_yielded} of {expected_bytes} bytes."
                                )
                        break
                    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ProtocolError) as e:
                        if not self.client or not self.url:
                            print(f"[Proxy] Stream error (no retry client/url) after {bytes_yielded} bytes: {repr(e)}")
                            break

                        expected_bytes = (end_byte - start_byte + 1) if end_byte is not None else None
                        if expected_bytes is not None and bytes_yielded >= expected_bytes:
                            break

                        retry_count += 1
                        total_reconnects += 1
                        if retry_count > max_retries:
                            print(f"[Proxy] Max retries reached ({max_retries}) after {bytes_yielded} bytes for {self.url[:60]}...")
                            break

                        # Only log on consecutive failures (retry_count > 1), not routine CDN drops
                        if retry_count > 1:
                            print(f"[Proxy] Consecutive retry ({retry_count}/{max_retries}) after {bytes_yielded} bytes for {self.url[:60]}...")

                        # Calculate new range
                        next_start = start_byte + bytes_yielded
                        range_header = f"bytes={next_start}-"
                        if end_byte is not None:
                            range_header += str(end_byte)

                        try:
                            await curr_resp.aclose()
                        except:
                            pass

                        # Prepare retry request
                        retry_headers = (self.headers_template or {}).copy()
                        retry_headers["range"] = range_header

                        try:
                            req = self.client.build_request("GET", self.url, headers=retry_headers, cookies=self.cookies)
                            curr_resp = await self.client.send(req, stream=True, follow_redirects=True)

                            if curr_resp.status_code in (403, 412, 514, 502, 504):
                                print(
                                    f"[Proxy] Retry got {curr_resp.status_code}; "
                                    f"refreshing ticket for {self.url[:60]}..."
                                )
                                await curr_resp.aclose()
                                retry_headers = await build_cdn_media_headers(forward_range=False, refresh_ticket=True)
                                retry_headers["range"] = range_header
                                req = self.client.build_request("GET", self.url, headers=retry_headers, cookies=self.cookies)
                                curr_resp = await self.client.send(req, stream=True, follow_redirects=True)
                                self.headers_template = retry_headers

                            if curr_resp.status_code != 206:
                                fail_status = curr_resp.status_code
                                await curr_resp.aclose()
                                if self._url_index + 1 < len(self._url_candidates):
                                    self._url_index += 1
                                    self.url = self._url_candidates[self._url_index]
                                    retry_count = max(0, retry_count - 1)
                                    print(
                                        f"[Proxy] Retry failed ({fail_status}); "
                                        f"switching to backup CDN ({self._url_index + 1}/{len(self._url_candidates)})"
                                    )
                                    retry_headers = await build_cdn_media_headers(forward_range=False)
                                    retry_headers["range"] = range_header
                                    req = self.client.build_request("GET", self.url, headers=retry_headers, cookies=self.cookies)
                                    curr_resp = await self.client.send(req, stream=True, follow_redirects=True)
                                    if curr_resp.status_code == 206:
                                        self.headers_template = retry_headers
                                        retry_content_range = curr_resp.headers.get("content-range")
                                        retry_skip = 0
                                        if retry_content_range:
                                            r_match = re.match(
                                                r"bytes\s+(\d+)-(\d+)",
                                                retry_content_range,
                                                re.IGNORECASE,
                                            )
                                            if r_match:
                                                actual_start = int(r_match.group(1))
                                                if actual_start < next_start:
                                                    retry_skip = next_start - actual_start
                                        skip_leading = retry_skip
                                        self.upstream_resp = curr_resp
                                        continue
                                print(
                                    f"[Proxy] Retry failed: upstream returned status {fail_status} instead of 206"
                                )
                                break

                            # Verify that the returned content-range matches next_start
                            retry_content_range = curr_resp.headers.get("content-range")
                            retry_skip = 0
                            if retry_content_range:
                                r_match = re.match(
                                    r"bytes\s+(\d+)-(\d+)", retry_content_range, re.IGNORECASE
                                )
                                if r_match:
                                    actual_start = int(r_match.group(1))
                                    if actual_start > next_start:
                                        print(
                                            f"[Proxy] Retry range gap: expected {next_start}, got {actual_start}"
                                        )
                                        await curr_resp.aclose()
                                        break
                                    if actual_start < next_start:
                                        retry_skip = next_start - actual_start

                            skip_leading = retry_skip
                            self.upstream_resp = curr_resp
                        except Exception as retry_err:
                            print(f"[Proxy] Retry connection attempt failed: {retry_err}")
                            break
                    except (asyncio.CancelledError, GeneratorExit):
                        raise
                    except Exception as e:
                        print(f"[Proxy] Stream unexpected error after {bytes_yielded} bytes: {type(e).__name__}: {e}")
                        break
            finally:
                try:
                    await curr_resp.aclose()
                except:
                    pass
                if total_reconnects > 0:
                    print(f"[Proxy] Stream finished: {bytes_yielded} bytes delivered, {total_reconnects} CDN reconnect(s).")

        super().__init__(response_generator(), status=status, *args, **kwargs)
        self.apply_stitch_range_headers()
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

    def apply_stitch_range_headers(self):
        """Align 206 Content-Range with the full stitched body the generator will deliver."""
        self.headers.pop("Content-Length", None)
        if self.stitch_end_byte is None or self.status_code != 206:
            return
        total = self.stitch_total_size or (self.stitch_end_byte + 1)
        self.headers["Content-Range"] = (
            f"bytes {self.stitch_start_byte}-{self.stitch_end_byte}/{total}"
        )

    async def aclose(self):
        """Called by Quart when the response is finished or the client disconnects."""
        await super().aclose()
        if self.upstream_resp:
            await self.upstream_resp.aclose()


def _parse_dash_url_entry(raw):
    """Redis value: plain URL string or JSON {primary, backup}."""
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = orjson.loads(raw)
        if isinstance(data, dict) and data.get("primary"):
            urls = [data["primary"], *(data.get("backup") or [])]
            return [u for u in urls if u]
    except orjson.JSONDecodeError:
        pass
    return [raw] if raw else []


def _finish_dash_proxy_response(proxy_resp, resp, media_type):
    proxy_resp.headers["Access-Control-Allow-Origin"] = "*"
    proxy_resp.headers["X-Accel-Buffering"] = "no"
    proxy_resp.apply_stitch_range_headers()
    if resp.status_code in (200, 206):
        ct = resp.headers.get("content-type", "").lower()
        if not ct or "application/octet-stream" in ct:
            proxy_resp.headers["Content-Type"] = "video/mp4" if media_type == "video" else "audio/mp4"
    return proxy_resp


@proxy_bp.route("/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>")
async def proxy_dash(vid, idx, media_type, qn, cid):
    raw = await appredis.get(f"miku_dash_url_{vid}_{idx}_{media_type}_{qn}_{cid}")
    urls = _parse_dash_url_entry(raw) if raw else []
    if not urls:
        return Response("Not Found", status=404)

    if not appconf["proxy"]["use_proxy"]:
        return Response("Forbidden: Proxying is disabled.", status=403)

    creds = appconf["credential"]
    cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

    client = await Network.get_async_client()

    async def fetch(url, refresh_ticket=False):
        headers = await build_cdn_media_headers(forward_range=True, refresh_ticket=refresh_ticket)
        req = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
        return await client.send(req, stream=True, follow_redirects=True)

    try:
        ticket_refreshed = False
        for url in urls:
            resp = await fetch(url)
            if resp.status_code in (403, 412, 514) and not ticket_refreshed:
                await resp.aclose()
                ticket_refreshed = True
                resp = await fetch(url, refresh_ticket=True)

            if resp.status_code in (200, 206):
                headers = await build_cdn_media_headers(forward_range=True)
                proxy_resp = ProxyResponse(
                    resp,
                    status=resp.status_code,
                    url=url,
                    headers=headers,
                    cookies=cookie_jar,
                    client=client,
                )
                try:
                    proxy_resp.call_on_close(resp.aclose)
                except AttributeError:
                    pass
                return _finish_dash_proxy_response(proxy_resp, resp, media_type)

            await resp.aclose()

        print(
            f"[Proxy] proxy_dash failed for {vid}:{idx} {media_type}/{qn}/{cid} "
            f"({len(urls)} url(s))"
        )
        return Response("Upstream Forbidden", status=403)
    except Exception as e:
        print(f"[Proxy] Error in proxy_dash: {e}")
        return Response(str(e), status=502)


@proxy_bp.route("/proxy/<path:subpath>")
async def proxy_main(subpath):
    req_path = f"/proxy/{subpath}"

    if req_path.startswith("/proxy/video/") or req_path.startswith("/proxy/live/"):
        is_live = "/proxy/live/" in req_path
        try:
            urls = []
            if is_live:
                parts = req_path.removeprefix("/proxy/live/").split("?")[0].split("_")
                room_id = parts[0]
                vqn = parts[1] if len(parts) > 1 else "default"
                redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
                raw_live = await appredis.get(redis_key)
                if not raw_live and vqn == "default":
                    fallback_keys = await appredis.keys(f"miku_live_{room_id}_*")
                    if fallback_keys:
                        raw_live = await appredis.get(fallback_keys[0])
                urls = _parse_dash_url_entry(raw_live) if raw_live else []
                url = urls[0] if urls else None
            else:
                path_to_split = req_path.removeprefix("/proxy/video/")
                if "." in path_to_split:
                    path_to_split = path_to_split.rsplit(".", 1)[0]
                vid, vidx, vqn = path_to_split.split("_")
                raw_url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")
                urls = _parse_dash_url_entry(raw_url) if raw_url else []
        except ValueError:
            return Response("Bad Request", status=400)

        if not urls:
            return Response("Not Found", status=404)

        if not appconf["proxy"]["use_proxy"]:
            return Response("Forbidden: Proxying is disabled.", status=403)

        creds = appconf["credential"]
        cookie_jar = {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}

        # SPECIAL HANDLING FOR LIVE FLV (Muxing/Multiplexing via LiveManager)
        if is_live and url and ".m3u8" not in url:
            live_headers = COMMON_HEADERS.copy()
            ticket = await TicketManager.get_ticket()
            if ticket:
                live_headers["x-bili-ticket"] = ticket
            client_id = request.args.get("cid") or str(uuid.uuid4())
            stream, q = await live_manager.subscribe(url, live_headers, cookie_jar, client_id)
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
        url = urls[0]
        fallback_urls = urls[1:]

        try:
            headers = await build_cdn_media_headers(forward_range=True)
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True, follow_redirects=True)
            print(f"[Proxy] Started direct stream: {url[:50]}... Status: {resp.status_code}")

            if resp.status_code in (403, 412, 514, 502, 504):
                print(
                    f"[Proxy] Direct stream failed with status {resp.status_code}. "
                    f"Refreshing ticket and retrying..."
                )
                await resp.aclose()
                headers = await build_cdn_media_headers(forward_range=True, refresh_ticket=True)
                proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
                resp = await client.send(proxy_request, stream=True, follow_redirects=True)
                print(f"[Proxy] Retried direct stream after ticket refresh: {url[:50]}... Status: {resp.status_code}")

            if resp.status_code not in (200, 206) and fallback_urls:
                await resp.aclose()
                for alt in fallback_urls:
                    headers = await build_cdn_media_headers(forward_range=True)
                    proxy_request = client.build_request("GET", alt, headers=headers, cookies=cookie_jar)
                    resp = await client.send(proxy_request, stream=True, follow_redirects=True)
                    print(f"[Proxy] Trying backup CDN: {alt[:50]}... Status: {resp.status_code}")
                    if resp.status_code in (200, 206):
                        url = alt
                        fallback_urls = [u for u in urls if u != alt]
                        break
                else:
                    return Response("Upstream Error", status=resp.status_code if resp else 502)

            proxy_resp = ProxyResponse(
                resp,
                status=resp.status_code,
                url=url,
                headers=headers,
                cookies=cookie_jar,
                client=client,
                fallback_urls=fallback_urls,
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
                    if k_lower in ("content-length", "content-range"):
                        continue
                    proxy_resp.headers[k] = v

            proxy_resp.apply_stitch_range_headers()

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
