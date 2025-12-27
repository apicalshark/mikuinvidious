import httpx, asyncio
from quart import Blueprint, request, Response
from urllib.parse import urlparse
from shared import appredis, appconf, Network, image_limiter

proxy_bp = Blueprint('proxy', __name__)

COMMON_HEADERS = {
    'referer': 'https://www.bilibili.com',
    'user-agent': 'Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)'
}

async def render_proxy_pic(req_path):
    async with image_limiter:
        req_path = req_path[11:]
        domain = req_path.split('/')[0]

        if not (domain.endswith('.hdslb.com') or domain.endswith('.biliimg.com')):
            return Response('Forbidden', status=403)

        headers = COMMON_HEADERS.copy()
        headers['host'] = domain
        url = f'https://{req_path}'
        
        client = Network.get_async_client()
        try:
            resp = await client.get(url, headers=headers, follow_redirects=True)
            return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))
        except Exception as e:
            return Response(str(e), status=502)

from live_manager import live_manager

@proxy_bp.route('/proxy/<path:subpath>')
async def proxy_main(subpath):
    req_path = f'/proxy/{subpath}'

    if req_path.startswith('/proxy/video/') or req_path.startswith('/proxy/live/'):
        is_live = '/proxy/live/' in req_path
        try:
            if is_live:
                parts = req_path.lstrip('/proxy/live/').split('?')[0].split('_')
                room_id = parts[0]
                vqn = parts[1] if len(parts) > 1 else "default"
                redis_key = f'miku_live_{room_id}_{vqn}' if vqn != "default" else f'miku_live_{room_id}'
                url = appredis.get(redis_key)
                if not url and vqn == "default":
                    fallback_keys = appredis.keys(f'miku_live_{room_id}_*')
                    if fallback_keys: url = appredis.get(fallback_keys[0])
            else:
                vid, vidx, vqn = req_path.lstrip('/proxy/video/').split('_')
                url = appredis.get(f'mikuinv_{vid}_{vidx}_{vqn}')
        except ValueError:
            return Response('Bad Request', status=400)
            
        if not url:
            return Response('Not Found', status=404)

        if isinstance(url, bytes): url = url.decode()
        urlp = urlparse(url)

        if not appconf['proxy']['use_proxy']:
            return Response('Forbidden: Proxying is disabled.', status=403)
        
        creds = appconf['credential']
        cookie_jar = {k: v for k, v in creds.items() if k != 'use_cred' and v} if creds['use_cred'] else {}

        headers = COMMON_HEADERS.copy()
        headers['host'] = urlp.netloc
        
        # Forward headers from client
        for k, v in request.headers.items():
            if k.lower() in ['range', 'if-range', 'x-playback-session-id']:
                headers[k.lower()] = v

        if is_live and '.flv' in url:
            # Use LiveStreamManager for FLV live streams
            print(f"[Proxy] Using LiveStreamManager for: {url[:50]}...")
            stream_obj, q = await live_manager.subscribe(url, headers, cookie_jar)
            
            if q is None:
                print(f"[Proxy] LiveStreamManager subscription failed for: {url[:50]}")
                return Response(f"Stream unavailable (Status {stream_obj.status_code})", status=stream_obj.status_code or 502)

            if stream_obj.status_code and stream_obj.status_code >= 400:
                print(f"[Proxy] LiveStreamManager returned error {stream_obj.status_code} for: {url[:50]}")
                live_manager.streams[url].remove_client(q)
                return Response(f"Stream error: {stream_obj.status_code}", status=stream_obj.status_code)

            async def generate():
                try:
                    while True:
                        chunk = await q.get()
                        if chunk is None: break
                        yield chunk
                except asyncio.CancelledError:
                    print(f"[Proxy] Connection cancelled for: {url[:50]}")
                    raise
                finally:
                    if url in live_manager.streams:
                        live_manager.streams[url].remove_client(q)

            proxy_resp = Response(generate(), status=stream_obj.status_code or 200)
            proxy_resp.headers['Content-Type'] = 'video/x-flv'
            for k, v in stream_obj.resp_headers.items():
                if k.lower() in ['accept-ranges', 'etag', 'last-modified']:
                    proxy_resp.headers[k] = v
            return proxy_resp

        if not is_live:
            limit_size = 1024 * 1024 # 1MB chunks for VOD
            range_val = headers.get('range')
            
            if range_val:
                try:
                    if range_val.startswith('bytes='):
                        r_spec = range_val.split('=')[1]
                        if '-' in r_spec:
                            r_parts = r_spec.split('-')
                            start = int(r_parts[0]) if r_parts[0] else 0
                            if r_parts[1]:
                                end = int(r_parts[1])
                                # Respect client end range if it's smaller than our limit
                                if end - start + 1 > limit_size:
                                    headers['range'] = f'bytes={start}-{start + limit_size - 1}'
                            else:
                                headers['range'] = f'bytes={start}-{start + limit_size - 1}'
                except: pass
            else:
                headers['range'] = f'bytes=0-{limit_size - 1}'

        client = Network.get_async_client()

        try:
            # Send the request and get the response stream
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True)
            print(f"[Proxy] Started stream: {url[:50]}... Status: {resp.status_code}")
            
            async def generate():
                try:
                    async for chunk in resp.aiter_bytes(chunk_size=1024*64):
                        yield chunk
                except asyncio.CancelledError:
                    print(f"[Proxy] Stream cancelled (client disconnected): {url[:50]}...")
                    return
                except GeneratorExit:
                    print(f"[Proxy] Generator exit: {url[:50]}...")
                except Exception as e:
                    print(f"[Proxy] Stream error: {e}")
                finally:
                    print(f"[Proxy] Closing stream: {url[:50]}...")
                    try:
                        # Use shield to ensure the stream is closed properly
                        await asyncio.shield(resp.aclose())
                    except: pass

            proxy_resp = Response(generate(), status=resp.status_code)
            
            # Set appropriate content type for live streams to help hls.js/flv.js
            if is_live:
                if '.m3u8' in url:
                    proxy_resp.headers['Content-Type'] = 'application/x-mpegURL'
                else:
                    proxy_resp.headers['Content-Type'] = 'video/x-flv'
            
            for k, v in resp.headers.items():
                if k.lower() in ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified']:
                    # Only override content-type if not already set for live
                    if is_live and k.lower() == 'content-type':
                        continue
                    proxy_resp.headers[k] = v
            return proxy_resp
        except Exception as e:
            return Response(str(e), status=502)

    elif req_path.startswith('/proxy/pic/'):
        return await render_proxy_pic(req_path)
    else:
        return Response('I\'m a teapot', status=418)
