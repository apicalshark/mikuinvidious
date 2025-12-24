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

import multiprocessing

from shared import *
from app import app
from flask.sessions import SecureCookieSessionInterface

from http.cookies import BaseCookie
from urllib.parse import quote as urlquote, urlparse, urlunparse
from twisted.web.http import _QUEUED_SENTINEL, HTTPChannel, HTTPClient, Request
from twisted.web.resource import Resource
from twisted.web.wsgi import WSGIResource
from twisted.web import proxy, server
from twisted.internet.protocol import ClientFactory
from twisted.internet import reactor, utils, ssl, tcp

plain_cookies = {}

################################################################################
# Modified Dynamic Proxy (from twisted)
################################################################################

class ProxyClient(HTTPClient):
    _finished = False

    def __init__(self, command, rest, version, headers, data, father):
        self.father = father
        self.command = command
        self.rest = rest
        if b"proxy-connection" in headers:
            del headers[b"proxy-connection"]
        headers[b"connection"] = b"close"
        headers.pop(b"keep-alive", None)
        self.headers = headers
        self.data = data

    def connectionMade(self):
        self.sendCommand(self.command, self.rest)
        for header, value in self.headers.items():
            self.sendHeader(header, value)
        self.endHeaders()
        self.transport.write(self.data)

    def handleStatus(self, version, code, message):
        self.father.setResponseCode(int(code), message)

    def handleHeader(self, key, value):
        if key.lower() in [b"server", b"date", b"content-type"]:
            self.father.responseHeaders.setRawHeaders(key, [value])
        else:
            self.father.responseHeaders.addRawHeader(key, value)

    def handleResponsePart(self, buffer):
        self.father.write(buffer)

    def handleResponseEnd(self):
        if not self._finished:
            self._finished = True
            self.father.notifyFinish().addErrback(lambda x: None)
            self.transport.loseConnection()

class ProxyClientFactory(ClientFactory):
    protocol = ProxyClient

    def __init__(self, command, rest, version, headers, data, father):
        self.father = father
        self.command = command
        self.rest = rest
        self.headers = headers
        self.data = data
        self.version = version

    def buildProtocol(self, addr):
        return self.protocol(
            self.command, self.rest, self.version, self.headers, self.data, self.father
        )

    def clientConnectionFailed(self, connector, reason):
        self.father.setResponseCode(501, b"Gateway error")
        self.father.responseHeaders.addRawHeader(b"Content-Type", b"text/html")
        self.father.write(b"<H1>Could not connect</H1>")
        self.father.finish()

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

    def render_proxy_pic(self, request, req_path):
        req_path = req_path[11:]
        domain = req_path.split('/')[0]

        if not domain.endswith('.hdslb.com'):
            request.setResponseCode(403)
            return

        request.requestHeaders.setRawHeaders(b'host', [domain.encode("ascii")])
        request.requestHeaders.setRawHeaders(b'user-agent', [b'Mozilla/5.0'
                                             b'BiliDroid/10.10.10 (bbcallen@gmail.com)'])
        request.content.seek(0, 0)

        clientFactory = ProxyClientFactory(
            b'GET', ('https://' + req_path).encode('utf-8'),
            request.clientproto,
            request.getAllHeaders(),
            request.content.read(),
            request,
        )

        self.reactor.connectSSL(domain, 443, clientFactory, ssl.ClientContextFactory())
        return server.NOT_DONE_YET

    def render(self, request):
        # Justify the request path.
        req_path = self.path.decode('utf-8')
        if req_path.startswith('/proxy/video/'):
            pass
        elif req_path.startswith('/proxy/pic/'):
            return self.render_proxy_pic(request, req_path)
        else:
            request.setResponseCode(418, b'I\'m a teapot')
            return

        # Parse and retrive the URL info.
        vid, vidx, vqn = req_path.lstrip('/proxy/video/').split('_')

        url = appredis.get(f'mikuinv_{vid}_{vidx}_{vqn}')
        if not url:
            request.setResponseCode(404, b'Not found')
            return

        url = url.decode()
        urlp = urlparse(url)

        # Direct Mode (use_proxy=False): Only Akamai is allowed (via redirect).
        # Non-Akamai mirrors are blocked because they require proxying to work.
        if not appconf['proxy']['use_proxy']:
            if urlp.netloc.endswith('-mirrorakam.akamaized.net'):
                request.setResponseCode(302)
                request.setHeader('Location', url)
                return b'Redirecting...'
            else:
                request.setResponseCode(403)
                return b'Forbidden: Direct connection only allowed for Akamai mirrors. Disable NO_PROXY to use server proxy.'

        request.requestHeaders.setRawHeaders(b'host', [urlp.netloc.encode("ascii")])
        if plain_cookies:
            request.requestHeaders.setRawHeaders('cookie', [plain_cookies])
        request.requestHeaders.setRawHeaders(b'referer', [b'https://www.bilibili.com'])
        request.requestHeaders.setRawHeaders(b'user-agent', [b'Mozilla/5.0'
                                             b'BiliDroid/10.10.10 (bbcallen@gmail.com)'])
        request.content.seek(0, 0)

        clientFactory = ProxyClientFactory(
            b'GET', url.encode('utf-8'),
            request.clientproto,
            request.getAllHeaders(),
            request.content.read(),
            request,
        )

        request.notifyFinish().addErrback(lambda x: clientFactory.doStop())

        nethost = urlp.netloc.split(':')[0] if ':' in urlp.netloc else urlp.netloc
        netport = int(urlp.netloc.split(':')[1]) if ':' in urlp.netloc else (80 if urlp.scheme == 'http' else 443)

        if urlp.scheme == 'http':
            self.reactor.connectTCP(nethost, netport, clientFactory)
        elif urlp.scheme == 'https':
            self.reactor.connectSSL(nethost, netport, clientFactory, ssl.ClientContextFactory())

        return server.NOT_DONE_YET

################################################################################

class MikuInvidiousResource(Resource):
    isLeaf = True

    def __init__(self):
        super().__init__()
        self.wsgi = WSGIResource(reactor, reactor.getThreadPool(), app)

    def render(self, request):
        if request.uri.startswith(b'/proxy'):
            return ReverseProxyResource(request.uri).render(request)
        return self.wsgi.render(request)

def main():
    # Intialize cookies.
    plain_cookies = appconf['credential']
    if plain_cookies['use_cred']:
        del plain_cookies['use_cred']
        cookiejar = ''
        for k, v in plain_cookies.items():
            cookiejar += f'{k}={v}; '
        plain_cookies = cookiejar[:-2]
    else:
        plain_cookies = False

    site = server.Site(MikuInvidiousResource())
    # reactor.listenTCP(appconf['twisted']['port'], site)  # only listens on ipv4
    port = tcp.Port(appconf['twisted']['port'], site, 50, appconf['twisted']['host'], reactor)
    port.startListening()
    reactor.run()

if __name__ == '__main__':
    main()
