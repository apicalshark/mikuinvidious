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
from io import BytesIO

from shared import *
from app import app
from flask.sessions import SecureCookieSessionInterface

from http.cookies import BaseCookie
from urllib.parse import quote as urlquote, urlparse, urlunparse
from twisted.web.http import _QUEUED_SENTINEL, HTTPChannel, Request
from twisted.web.resource import Resource
from twisted.web.wsgi import WSGIResource
from twisted.web import proxy, server
from twisted.internet.protocol import ClientFactory, Protocol
from twisted.internet import reactor, utils, ssl, tcp
from twisted.web.client import Agent, ResponseDone, HTTPConnectionPool, FileBodyProducer, BrowserLikePolicyForHTTPS
from twisted.web.http_headers import Headers
from twisted.internet.endpoints import HostnameEndpoint, TCP4ClientEndpoint, wrapClientTLS
from twisted.internet.defer import Deferred, succeed
from zope.interface import implementer
from twisted.internet.interfaces import IStreamClientEndpoint

plain_cookies = ""

################################################################################
# SOCKS5 Proxy Implementation (Modern Endpoint version)
################################################################################

class Socks5HandshakeProtocol(Protocol):
    def __init__(self, host, port, protocolFactory, deferred):
        self.host = host
        self.port = port
        self.protocolFactory = protocolFactory
        self.deferred = deferred
        self.state = 0 # 0: Init, 1: Connecting, 2: Connected
        self._buffer = b''

    def connectionMade(self):
        # 1. Send Hello (Version 5, 1 auth method: No Auth)
        self.transport.write(b'\x05\x01\x00')

    def dataReceived(self, data):
        if self.state == 2:
            # If we are already switched but still getting data here, forward it
            if self.transport.protocol is not self:
                self.transport.protocol.dataReceived(data)
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
            
            extra_data = self._buffer[resp_len:]
            self._buffer = b''
            
            if rep != 0:
                self.transport.loseConnection()
                return
            
            # Handshake success.
            self.state = 2
            
            # Switch to the real protocol requested by Agent (e.g. TLS or HTTP)
            protocol = self.protocolFactory.buildProtocol(self.transport.getPeer())
            self.transport.protocol = protocol
            protocol.makeConnection(self.transport)
            
            # Notify Agent that connection is ready
            self.deferred.callback(protocol)
            
            if extra_data:
                protocol.dataReceived(extra_data)

class Socks5HandshakeFactory(ClientFactory):
    def __init__(self, host, port, protocolFactory, deferred):
        self.host = host
        self.port = port
        self.protocolFactory = protocolFactory
        self.deferred = deferred

    def buildProtocol(self, addr):
        return Socks5HandshakeProtocol(self.host, self.port, self.protocolFactory, self.deferred)

    def clientConnectionFailed(self, connector, reason):
        if not self.deferred.called:
            self.deferred.errback(reason)

@implementer(IStreamClientEndpoint)
class SOCKS5ClientEndpoint:
    def __init__(self, reactor, proxyHost, proxyPort, targetHost, targetPort):
        self.reactor = reactor
        self.proxyHost = proxyHost
        self.proxyPort = proxyPort
        self.targetHost = targetHost
        self.targetPort = targetPort

    def connect(self, protocolFactory):
        d = Deferred()
        f = Socks5HandshakeFactory(self.targetHost, self.targetPort, protocolFactory, d)
        self.reactor.connectTCP(self.proxyHost, self.proxyPort, f)
        return d

class Socks5EndpointFactory:
    def __init__(self, reactor, proxy_url):
        self.reactor = reactor
        self.use_socks = False
        self.policy = BrowserLikePolicyForHTTPS()
        if appconf['proxy']['use_proxy'] and proxy_url and proxy_url.startswith('socks5://'):
            try:
                p = urlparse(proxy_url)
                self.proxy_host = p.hostname
                self.proxy_port = p.port or 1080
                self.use_socks = True
            except:
                pass

    def endpointForURI(self, uri):
        if self.use_socks:
            endpoint = SOCKS5ClientEndpoint(self.reactor, self.proxy_host, self.proxy_port, 
                                        uri.host.decode('ascii'), uri.port)
        else:
            endpoint = HostnameEndpoint(self.reactor, uri.host.decode('ascii'), uri.port)
        
        if uri.scheme == b'https':
            # Wrap the SOCKS5/TCP endpoint with TLS for HTTPS URIs
            contextFactory = self.policy.creatorForNetloc(uri.host, uri.port)
            endpoint = wrapClientTLS(contextFactory, endpoint)
            
        return endpoint

################################################################################
# Agent Response Forwarder
################################################################################

class ResponseForwarder(Protocol):
    def __init__(self, father):
        self.father = father

    def dataReceived(self, data):
        self.father.write(data)

    def connectionLost(self, reason):
        if not self.father.finished:
            try:
                self.father.finish()
            except RuntimeError:
                # Request might have been finished/lost just now
                pass

################################################################################
# Modified Dynamic Proxy (Modern Agent based)
################################################################################

class ReverseProxyResource(Resource):
    def __init__(self, path, reactor=reactor):
        Resource.__init__(self)
        self.path = path
        self.reactor = reactor
        self.agent = Agent.usingEndpointFactory(self.reactor, 
                                                Socks5EndpointFactory(self.reactor, os.environ.get('HTTP_PROXY')))

    def getChild(self, path, request):
        return ReverseProxyResource(
            self.path + b'/' + urlquote(path, safe=b'').encode("utf-8"),
            self.reactor
        )

    def _cbResponse(self, response, request):
        request.setResponseCode(response.code)
        for name, values in response.headers.getAllRawHeaders():
            if name.lower() in [b"server", b"date", b"content-type"]:
                request.responseHeaders.setRawHeaders(name, values)
            else:
                for value in values:
                    request.responseHeaders.addRawHeader(name, value)
        
        response.deliverBody(ResponseForwarder(request))
        return server.NOT_DONE_YET

    def _ebResponse(self, failure, request):
        request.setResponseCode(501, b"Gateway error")
        request.responseHeaders.addRawHeader(b"Content-Type", b"text/html")
        request.write(b"<H1>Could not connect</H1>")
        request.write(f"<p>{failure.getErrorMessage()}</p>".encode())
        request.finish()

    def render_proxy_pic(self, request, req_path):
        req_path = req_path[11:]
        domain = req_path.split('/')[0]

        if not domain.endswith('.hdslb.com'):
            request.setResponseCode(403)
            return

        headers = Headers()
        for name, values in request.requestHeaders.getAllRawHeaders():
            headers.setRawHeaders(name, values)
        
        headers.setRawHeaders(b'host', [domain.encode("ascii")])
        headers.setRawHeaders(b'user-agent', [b'Mozilla/5.0'
                                              b'BiliDroid/8.76.0 (bbcallen@gmail.com)'])
        
        # Agent uses IBodyProducer for content
        body = None
        if request.content:
            request.content.seek(0, 0)
            content = request.content.read()
            if content:
                body = FileBodyProducer(BytesIO(content)) if 'BytesIO' in globals() else None
                # Fallback if BytesIO not imported, but pic GET usually has no body
        
        d = self.agent.request(
            b'GET',
            ('https://' + req_path).encode('utf-8'),
            headers,
            body
        )
        d.addCallback(self._cbResponse, request)
        d.addErrback(self._ebResponse, request)
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
        try:
            vid, vidx, vqn = req_path.lstrip('/proxy/video/').split('_')
        except ValueError:
            request.setResponseCode(400)
            return b'Invalid request'

        url = appredis.get(f'mikuinv_{vid}_{vidx}_{vqn}')
        if not url:
            request.setResponseCode(404, b'Not found')
            return

        if isinstance(url, bytes):
            url = url.decode()
        urlp = urlparse(url)

        # Direct Mode (use_proxy=False): Only Akamai is allowed (via redirect).
        if not appconf['proxy']['use_proxy']:
            if urlp.netloc.endswith('-mirrorakam.akamaized.net'):
                request.setResponseCode(302)
                request.setHeader('Location', url)
                return b'Redirecting...'
            else:
                request.setResponseCode(403)
                return b'Forbidden: Direct connection only allowed for Akamai mirrors. Disable NO_PROXY to use server proxy.'

        headers = Headers()
        for name, values in request.requestHeaders.getAllRawHeaders():
            if name.lower() not in [b'host', b'referer', b'user-agent', b'cookie']:
                headers.setRawHeaders(name, values)

        headers.setRawHeaders(b'host', [urlp.netloc.encode("ascii")])
        if plain_cookies:
            headers.setRawHeaders(b'cookie', [plain_cookies.encode() if isinstance(plain_cookies, str) else plain_cookies])
        headers.setRawHeaders(b'referer', [b'https://www.bilibili.com'])
        headers.setRawHeaders(b'user-agent', [b'Mozilla/5.0'
                                             b'BiliDroid/8.76.0 (bbcallen@gmail.com)'])
        
        d = self.agent.request(
            b'GET',
            url.encode('utf-8'),
            headers,
            None # Video requests here are GET without body
        )
        
        d.addCallback(self._cbResponse, request)
        d.addErrback(self._ebResponse, request)

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
    # reactor.listenTCP(appconf['twisted']['port'], site)  # only listens on ipv4
    port = tcp.Port(appconf['twisted']['port'], site, 50, appconf['twisted']['host'], reactor)
    port.startListening()
    reactor.run()

if __name__ == '__main__':
    main()
