import asyncio
import json
import uuid
from urllib.parse import urlparse

from extra import video_get_dash_for_qn, video_get_src_for_qn
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
            while self._current_url_index < len(self.urls):
                url = self.urls[self._current_url_index]
                client = await Network.get_async_client()

                try:
                    proxy_request = client.build_request("GET", url, headers=self.client_headers, cookies=self.cookies)
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
                    else:
                        print(
                            f"[Proxy] CDN {self._current_url_index} returned {self.upstream_resp.status_code}, trying next..."
                        )
                        await self.upstream_resp.aclose()
                except Exception as e:
                    print(f"[Proxy] CDN {self._current_url_index} failed with error: {e}")
                    if self.upstream_resp:
                        await self.upstream_resp.aclose()

                self._current_url_index += 1

            print("[Proxy] All CDNs exhausted.")

        super().__init__(response_generator(), *args, **kwargs)

    async def aclose(self):
        """Called by Quart when the response is finished or the client disconnects."""
        await super().aclose()
        if self.upstream_resp:
            await self.upstream_resp.aclose()


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

        # FALLBACK TO 360P IF 404
        try:
            if probe_resp.status_code == 404 and qn > 16:
                print(f"[Proxy-Dash] 404 detected for QN {qn}, falling back to 360P...")
                referer = request.headers.get("referer", "")
                import re

                vid_match = re.search(r"BV[a-zA-Z0-9]+", referer)
                if vid_match:
                    vid = vid_match.group(0)
                    from bilibili_api import video as b_video
                    from extra import video_get_dash_for_qn
                    from shared import appcred

                    vi = b_video.Video(bvid=vid, credential=get_current_cred())
                    fallback_res = await video_get_dash_for_qn(vi, 0)
                    if fallback_res and "dash" in fallback_res:
                        tracks = fallback_res["dash"].get(media_type, [])
                        if tracks:
                            target_track = next((t for t in tracks if t["id"] == 16), tracks[-1])
                            new_urls = [target_track.get("baseUrl") or target_track.get("base_url")]
                            if "backupUrl" in target_track and target_track["backupUrl"]:
                                new_urls.extend(target_track["backupUrl"])
                            new_urls = [u for u in new_urls if u]
                            if new_urls:
                                urls = new_urls
                                await probe_resp.aclose()
                                probe_resp = None  # Clear to avoid double close in finally
                                proxy_request = client.build_request(
                                    "GET", urls[0], headers=headers, cookies=cookie_jar
                                )
                                probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
                                print(
                                    f"[Proxy-Dash] Fallback probe response: {urls[0][:50]}... Status: {probe_resp.status_code}"
                                )

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
                                # Type 18 (Script Data), Size 0, Time 0, PrevSize 11
                                # This acts as a keep-alive to prevent Nginx/Hypercorn timeout
                                yield b"\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b"
                            continue
                        except Exception as e:
                            reason = f"Inner generator error: {e}"
                            break
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

        # DIRECT PROXY FOR VOD AND HLS LIVE
        client = await Network.get_async_client()
        probe_resp = None

        try:
            # Probe the first URL to get headers
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
            print(f"[Proxy] Probe response: {url[:50]}... Status: {probe_resp.status_code}")

            # FALLBACK TO 360P IF 404
            if probe_resp.status_code == 404 and vqn > 16:
                print(f"[Proxy] 404 detected for QN {vqn}, falling back to 360P...")
                from bilibili_api import video as b_video
                from extra import video_get_src_for_qn
                from shared import appcred

                vi = b_video.Video(bvid=vid, credential=get_current_cred())
                fallback_res = await video_get_src_for_qn(vi, vidx, 16)
                if fallback_res and "durl" in fallback_res:
                    urls = [d["url"] for d in fallback_res["durl"] if d.get("url")]
                    if urls:
                        url = urls[0]
                        await probe_resp.aclose()
                        probe_resp = None  # Clear to avoid double close in finally
                        proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
                        probe_resp = await client.send(proxy_request, stream=True, follow_redirects=True)
                        print(f"[Proxy] Fallback probe response: {url[:50]}... Status: {probe_resp.status_code}")

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
