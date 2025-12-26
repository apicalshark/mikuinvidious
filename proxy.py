# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import httpx
from flask import Blueprint, request, Response
from urllib.parse import urlparse
from shared import appredis, appconf, get_global_httpx_client, image_limiter

proxy_bp = Blueprint('proxy', __name__)

async def render_proxy_pic(req_path):
    await image_limiter.acquire()
    req_path = req_path[11:]
    domain = req_path.split('/')[0]

    if not domain.endswith('.hdslb.com'):
        return Response('Forbidden', status=403)

    headers = {
        'Host': domain,
        'Referer': 'https://www.bilibili.com',
        'User-Agent': 'Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)'
    }
    url = f'https://{req_path}'
    
    client = get_global_httpx_client()
    resp = await client.get(url, headers=headers, follow_redirects=True)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@proxy_bp.route('/proxy/<path:subpath>')
async def proxy_main(subpath):
    req_path = f'/proxy/{subpath}'

    if req_path.startswith('/proxy/video/'):
        try:
            vid, vidx, vqn = req_path.lstrip('/proxy/video/').split('_')
        except ValueError:
            return Response('Bad Request', status=400)
            
        url = appredis.get(f'mikuinv_{vid}_{vidx}_{vqn}')
        if not url:
            return Response('Not Found', status=404)

        urlp = urlparse(url)

        if not appconf['proxy']['use_proxy']:
            if urlp.netloc.endswith('-mirrorakam.akamaized.net'):
                return Response(status=302, headers={'Location': url})
            else:
                return Response('Forbidden: Direct connection only allowed for Akamai mirrors.', status=403)
        
        plain_cookies = appconf['credential']
        cookie_jar = {}
        if plain_cookies['use_cred']:
            cookie_jar = {k: v for k, v in plain_cookies.items() if k != 'use_cred'}

        headers = {
            'Host': urlp.netloc,
            'Referer': 'https://www.bilibili.com',
            'User-Agent': 'Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)'
        }
        
        # We use a stream for video data
        async def generate():
            client = get_global_httpx_client()
            async with client.stream("GET", url, headers=headers, cookies=cookie_jar, follow_redirects=True) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=1024*128):
                    yield chunk

        # Get initial response for headers
        client = get_global_httpx_client()
        async with client.stream("GET", url, headers=headers, cookies=cookie_jar, follow_redirects=True) as resp:
            return Response(generate(), status=resp.status_code, content_type=resp.headers.get('content-type'))

    elif req_path.startswith('/proxy/pic/'):
        return await render_proxy_pic(req_path)
    else:
        return Response('I\'m a teapot', status=418)