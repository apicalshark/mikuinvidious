import httpx, asyncio
from quart import Blueprint, request, Response, stream_with_context
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
            if urlp.netloc.endswith('-mirrorakam.akamaized.net'):
                return Response(status=302, headers={'Location': url})
            else:
                return Response('Forbidden: Direct connection only allowed for Akamai mirrors.', status=403)
        
        creds = appconf['credential']
        cookie_jar = {k: v for k, v in creds.items() if k != 'use_cred' and v} if creds['use_cred'] else {}

        headers = COMMON_HEADERS.copy()
        headers['host'] = urlp.netloc
        
        # Forward headers from client
        for k, v in request.headers.items():
            if k.lower() in ['range', 'if-range', 'x-playback-session-id']:
                headers[k.lower()] = v

        if not is_live:
            limit_size = 512 * 1024 # 512KB
            range_val = headers.get('range')
            
            if range_val:
                try:
                    if range_val.startswith('bytes='):
                        r_spec = range_val.split('=')[1]
                        if '-' in r_spec:
                            r_parts = r_spec.split('-')
                            if not r_parts[1]:
                                start = int(r_parts[0]) if r_parts[0] else 0
                                headers['range'] = f'bytes={start}-{start + limit_size - 1}'
                except: pass
            else:
                headers['range'] = 'bytes=0-524287' # 512KB initial chunk

        client = Network.get_async_client()

        try:
            # Send the request and get the response stream
            proxy_request = client.build_request("GET", url, headers=headers, cookies=cookie_jar)
            resp = await client.send(proxy_request, stream=True)
            print(f"[Proxy] Started stream: {url[:50]}... Status: {resp.status_code}")
            
            @stream_with_context
            async def generate():
                try:
                    async for chunk in resp.aiter_bytes(chunk_size=1024*64):
                        yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    print(f"[Proxy] Stream cancelled/exit: {url[:50]}...")
                except Exception as e:
                    print(f"[Proxy] Stream error: {e}")
                finally:
                    print(f"[Proxy] Closing stream: {url[:50]}...")
                    await resp.aclose()

            proxy_resp = Response(generate(), status=resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() in ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified']:
                    proxy_resp.headers[k] = v
            return proxy_resp
        except Exception as e:
            return Response(str(e), status=502)

    elif req_path.startswith('/proxy/pic/'):
        return await render_proxy_pic(req_path)
    else:
        return Response('I\'m a teapot', status=418)
