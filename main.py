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

import sys
from twisted.internet import asyncioreactor
if 'twisted.internet.reactor' not in sys.modules:
    asyncioreactor.install()

import multiprocessing
import asyncio
from io import BytesIO

from shared import *
from app import app

from urllib.parse import quote as urlquote, urlparse
from twisted.web.resource import Resource
from twisted.web.wsgi import WSGIResource
from twisted.web import server
from twisted.internet import reactor, tcp
from twisted.internet.defer import Deferred

plain_cookies = ""

################################################################################
# Modified Dynamic Proxy (httpx based)
################################################################################

class ReverseProxyResource(Resource):
    def __init__(self, path, reactor=reactor):
        Resource.__init__(self)
        self.path = path
        self.reactor = reactor

    def getChild(self, path, request):
        return ReverseProxyResource(
            self.path + b'/' + urlquote(path, safe=b'').encode("utf-8"),
            self.reactor
        )

    async def _async_render_proxy_pic(self, request, req_path):
        await image_limiter.acquire()
        client = get_global_httpx_client()
        req_path_stripped = req_path[11:]
        domain = req_path_stripped.split('/')[0]

        if not domain.endswith('.hdslb.com'):
            request.setResponseCode(403)
            if not request.finished:
                request.finish()
            return

        headers = {}
        for name, values in request.requestHeaders.getAllRawHeaders():
            if name.lower() not in [b'host', b'referer', b'user-agent', b'cookie', b'connection']:
                headers[name.decode('ascii')] = values[0].decode('ascii')
        
        headers['host'] = domain
        headers['referer'] = 'https://www.bilibili.com'
        headers['user-agent'] = 'Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)'
        
        # Track connection status
        finished = [False]
        def _set_finished(_):
            finished[0] = True
        request.notifyFinish().addBoth(_set_finished)

        try:
            async with client.stream('GET', 'https://' + req_path_stripped, headers=headers, follow_redirects=True) as resp:
                if finished[0]: return
                request.setResponseCode(resp.status_code)
                for name, value in resp.headers.items():
                    if name.lower() not in ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade']:
                        request.responseHeaders.addRawHeader(name.encode('ascii'), value.encode('ascii'))
                
                async for chunk in resp.aiter_bytes():
                    if finished[0]: break
                    request.write(chunk)
                
                if not finished[0]:
                    request.finish()
        except Exception as e:
            if not finished[0]:
                self._handle_error(e, request)

    async def _async_render_proxy_video(self, request, url, urlp):
        client = get_global_httpx_client()
        headers = {}
        for name, values in request.requestHeaders.getAllRawHeaders():
            if name.lower() not in [b'host', b'referer', b'user-agent', b'cookie', b'connection']:
                headers[name.decode('ascii')] = values[0].decode('ascii')

        # Force a Range limit to prevent excessive background buffering
        limit_size = 512 * 1024 # 512KB
        range_val = request.getHeader(b'range')
        
        if range_val:
            try:
                range_str = range_val.decode('ascii')
                if range_str.startswith('bytes='):
                    r_spec = range_str.split('=')[1]
                    if '-' in r_spec:
                        r_parts = r_spec.split('-')
                        if not r_parts[1]:
                            start = int(r_parts[0]) if r_parts[0] else 0
                            headers['range'] = f'bytes={start}-{start + limit_size - 1}'
                        else:
                            headers['range'] = range_str
            except:
                headers['range'] = range_val.decode('ascii') if isinstance(range_val, bytes) else range_val

        headers['host'] = urlp.netloc
        if plain_cookies:
            headers['cookie'] = plain_cookies
        headers['referer'] = 'https://www.bilibili.com'
        headers['user-agent'] = 'Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com)'
        
        # Track connection status
        finished = [False]
        def _set_finished(_):
            finished[0] = True
        request.notifyFinish().addBoth(_set_finished)

        try:
            async with client.stream('GET', url, headers=headers, follow_redirects=True) as resp:
                if finished[0]: return
                request.setResponseCode(resp.status_code)
                for name, value in resp.headers.items():
                    if name.lower() not in ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade']:
                        request.responseHeaders.addRawHeader(name.encode('ascii'), value.encode('ascii'))
                
                async for chunk in resp.aiter_bytes():
                    if finished[0]: break
                    request.write(chunk)
                
                if not finished[0]:
                    request.finish()
        except Exception as e:
            if not finished[0]:
                self._handle_error(e, request)

    def _handle_error(self, e, request):
        if not getattr(request, 'finished', False):
            try:
                request.setResponseCode(501, b"Gateway error")
                request.responseHeaders.addRawHeader(b"Content-Type", b"text/html")
                request.write(b"<H1>Could not connect</H1>")
                request.write(f"<p>{str(e)}</p>".encode())
                request.finish()
            except RuntimeError:
                pass # Already finished or lost connection

    def render(self, request):
        req_path = request.uri.decode('utf-8')
        if req_path.startswith('/proxy/video/'):
            try:
                vid, vidx, vqn = req_path.lstrip('/proxy/video/').split('_')
            except ValueError:
                request.setResponseCode(400)
                return b'Invalid request'

            url = appredis.get(f'mikuinv_{vid}_{vidx}_{vqn}')
            if not url:
                request.setResponseCode(404, b'Not found')
                return b'Not found'

            if isinstance(url, bytes):
                url = url.decode()
            urlp = urlparse(url)

            if not appconf['proxy']['use_proxy']:
                if urlp.netloc.endswith('-mirrorakam.akamaized.net'):
                    request.setResponseCode(302)
                    request.setHeader('Location', url)
                    return b'Redirecting...'
                else:
                    request.setResponseCode(403)
                    return b'Forbidden: Direct connection only allowed for Akamai mirrors. Disable NO_PROXY to use server proxy.'

            asyncio.ensure_future(self._async_render_proxy_video(request, url, urlp))
            return server.NOT_DONE_YET

        elif req_path.startswith('/proxy/pic/'):
            asyncio.ensure_future(self._async_render_proxy_pic(request, req_path))
            return server.NOT_DONE_YET
        else:
            request.setResponseCode(418, b'I\'m a teapot')
            return b''

################################################################################

class MikuInvidiousResource(Resource):
    isLeaf = True

    def __init__(self):
        super().__init__()
        self.proxy_resource = ReverseProxyResource(b'')
        self.wsgi = WSGIResource(reactor, reactor.getThreadPool(), app)

    def render(self, request):
        if request.uri.startswith(b'/proxy'):
            return self.proxy_resource.render(request)
        return self.wsgi.render(request)

def main():
    # Intialize cookies.
    global plain_cookies
    creds = appconf['credential']
    if creds['use_cred']:
        cookiejar = ''
        for k, v in creds.items():
            if k != 'use_cred' and v:
                cookiejar += f'{k}={v}; '
        plain_cookies = cookiejar[:-2]
    else:
        plain_cookies = ""

    site = server.Site(MikuInvidiousResource())
    port = tcp.Port(appconf['twisted']['port'], site, 50, appconf['twisted']['host'], reactor)
    port.startListening()
    reactor.run()

if __name__ == '__main__':
    main()