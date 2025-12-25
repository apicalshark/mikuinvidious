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
import struct

from shared import *
from app import app
from flask.sessions import SecureCookieSessionInterface

from http.cookies import BaseCookie
from urllib.parse import quote as urlquote, urlparse, urlunparse
from twisted.web.http import _QUEUED_SENTINEL, HTTPChannel, HTTPClient, Request
from twisted.web.resource import Resource
from twisted.web.wsgi import WSGIResource
from twisted.web import proxy, server
from twisted.internet.protocol import ClientFactory, Protocol
from twisted.internet import reactor, utils, ssl, tcp

plain_cookies = {}

################################################################################
# SOCKS5 Proxy Implementation
################################################################################

class Socks5Client(Protocol):
    def __init__(self, host, port, original_factory, context_factory=None):
        self.host = host
        self.port = port
        self.original_factory = original_factory
        self.context_factory = context_factory
        self.state = 0 # 0: Init, 1: Connecting, 2: Connected
        self._buffer = b''
        self.protocol = None

    def connectionMade(self):
        # 1. Send Hello (Version 5, 1 auth method: No Auth)
        self.transport.write(b'\x05\x01\x00')

    def dataReceived(self, data):
        if self.state == 2:
            if self.protocol:
                self.protocol.dataReceived(data)
            return

        self._buffer += data
        
        if self.state == 0:
            if len(self._buffer) < 2: return
            ver, method = self._buffer[0], self._buffer[1]
            self._buffer = self._buffer[2:]
            
            if ver != 5 or method != 0:
                self.transport.loseConnection()
                return
            
            # 2. Request Connect
            # VER CMD RSV ATYP DST.ADDR DST.PORT
            # ATYP: 0x03 (Domain name)
            host_bytes = self.host.encode()
            req = b'\x05\x01\x00\x03' + bytes([len(host_bytes)]) + host_bytes + struct.pack('!H', self.port)
            self.transport.write(req)
            self.state = 1
            
        elif self.state == 1:
            if len(self._buffer) < 4: return
            ver, rep, rsv, atyp = self._buffer[0], self._buffer[1], self._buffer[2], self._buffer[3]
            
            resp_len = 4
            if atyp == 1: resp_len += 6 # IPv4
            elif atyp == 3: 
                if len(self._buffer) < 5: return
                resp_len += 1 + self._buffer[4] + 2
            elif atyp == 4: resp_len += 18 # IPv6
            
            if len(self._buffer) < resp_len: return
            
            # Consume response
            extra_data = self._buffer[resp_len:]
            self._buffer = b''
            
            if rep != 0:
                self.transport.loseConnection()
                return
            
            # Handshake success.
            self.state = 2
            
            # If TLS is required, start it now over the tunnel
            if self.context_factory:
                self.transport.startTLS(self.context_factory)
            
            # Build the wrapped protocol
            self.protocol = self.original_factory.buildProtocol(self.transport.getPeer())
            self.protocol.makeConnection(self.transport)
            
            if extra_data:
                self.protocol.dataReceived(extra_data)

    def connectionLost(self, reason):
        if self.protocol:
            self.protocol.connectionLost(reason)

class Socks5ClientFactory(ClientFactory):
    def __init__(self, host, port, original_factory, context_factory=None):
        self.host = host
        self.port = port
        self.original_factory = original_factory
        self.context_factory = context_factory

    def buildProtocol(self, addr):
        return Socks5Client(self.host, self.port, self.original_factory, self.context_factory)

    def clientConnectionFailed(self, connector, reason):
        self.original_factory.clientConnectionFailed(connector, reason)

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

    def _connect_to_remote(self, host, port, factory, context_factory=None):
        proxy_url = os.environ.get('HTTP_PROXY')
        use_socks = False
        socks_host = ''
        socks_port = 1080

        if appconf['proxy']['use_proxy'] and proxy_url and proxy_url.startswith('socks5://'):
             try:
                 use_socks = True
                 p = urlparse(proxy_url)
                 socks_host = p.hostname
                 socks_port = p.port or 1080
             except:
                 use_socks = False

        if use_socks:
             sf = Socks5ClientFactory(host, port, factory, context_factory)
             self.reactor.connectTCP(socks_host, socks_port, sf)
        else:
             if context_factory:
                 self.reactor.connectSSL(host, port, factory, context_factory)
             else:
                 self.reactor.connectTCP(host, port, factory)

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

        self._connect_to_remote(domain, 443, clientFactory, ssl.ClientContextFactory())
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

        if isinstance(url, bytes):
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

        context_factory = None
        if urlp.scheme == 'https':
            context_factory = ssl.ClientContextFactory()
        
        self._connect_to_remote(nethost, netport, clientFactory, context_factory)

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
