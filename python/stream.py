import asyncio
import ssl
from urllib.parse import urlparse


class CdnConnectError(Exception):
    pass


class CdnProtocolError(Exception):
    pass


class CdnTimeoutError(Exception):
    pass


def _parse_proxy_url(proxy_url):
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    if not port:
        port = 1080 if scheme.startswith("socks") else 3128
    return scheme, host, port


def _parse_cdn_url(url):
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    use_tls = parsed.scheme.lower() == "https"
    if not port:
        port = 443 if use_tls else 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return host, port, path, use_tls


async def _socks5_handshake(writer, reader, target_host, target_port, timeout):
    async with asyncio.timeout(timeout):
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        resp = await reader.readexactly(2)
        if resp[0] != 5:
            raise CdnConnectError(f"SOCKS5: bad version {resp[0]}")
        if resp[1] != 0:
            raise CdnConnectError(f"SOCKS5: auth method {resp[1]} required")

        target_bytes = target_host.encode("idna")
        msg = b"\x05\x01\x00\x03" + bytes([len(target_bytes)]) + target_bytes + target_port.to_bytes(2, "big")
        writer.write(msg)
        await writer.drain()
        resp = await reader.readexactly(4)
        if resp[0] != 5:
            raise CdnConnectError(f"SOCKS5: bad reply version {resp[0]}")
        if resp[1] != 0:
            raise CdnConnectError(f"SOCKS5: connect failed status {resp[1]}")
        atype = resp[3]
        if atype == 1:
            await reader.readexactly(6)
        elif atype == 3:
            dlen = (await reader.readexactly(1))[0]
            await reader.readexactly(dlen + 2)
        elif atype == 4:
            await reader.readexactly(18)
        else:
            raise CdnConnectError(f"SOCKS5: unknown address type {atype}")


async def _http_connect_tunnel(writer, reader, target_host, target_port, timeout):
    req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    async with asyncio.timeout(timeout):
        writer.write(req)
        await writer.drain()
        line = await _read_line(reader)
        try:
            status = int(line.split(b" ")[1])
        except (ValueError, IndexError) as e:
            raise CdnProtocolError(f"Bad HTTP CONNECT status line: {line!r}") from e
        if status != 200:
            raise CdnConnectError(f"HTTP CONNECT failed: {line.decode(errors='replace').strip()}")
        while True:
            hline = await _read_line(reader)
            if hline == b"":
                raise CdnProtocolError("HTTP CONNECT connection closed prematurely")
            if hline in (b"\r\n", b"\n"):
                break


async def _read_line(reader):
    return await reader.readline()


class CdnResponse:
    def __init__(self):
        self.status_code = 0
        self.reason = ""
        self.headers = {}
        self.http_version = ""


class CdnConnection:
    def __init__(
        self,
        url: str,
        headers: dict | None = None,
        proxy_url: str | None = None,
        read_chunk_size: int = 256 * 1024,
        connect_timeout: float = 15.0,
        read_timeout: float | None = None,
    ):
        self._url = url
        self._headers = headers or {}
        self._proxy_url = proxy_url
        self._read_chunk_size = read_chunk_size
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._host, self._port, self._path, self._use_tls = _parse_cdn_url(url)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._response = CdnResponse()

    async def connect(self):
        async with asyncio.timeout(self._connect_timeout):
            if self._proxy_url:
                proxy_scheme, proxy_host, proxy_port = _parse_proxy_url(self._proxy_url)
                self._reader, self._writer = await asyncio.open_connection(proxy_host, proxy_port)
                if proxy_scheme.startswith("socks"):
                    await _socks5_handshake(self._writer, self._reader, self._host, self._port, self._connect_timeout)
                elif proxy_scheme == "http":
                    await _http_connect_tunnel(
                        self._writer, self._reader, self._host, self._port, self._connect_timeout
                    )
                else:
                    raise CdnConnectError(f"Unsupported proxy scheme: {proxy_scheme}")

                if self._use_tls:
                    ctx = ssl.create_default_context()
                    await self._writer.start_tls(ctx, server_hostname=self._host)
            else:
                ctx = ssl.create_default_context() if self._use_tls else None
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port, ssl=ctx, server_hostname=self._host if ctx else None
                )

        self._connected = True

    async def send_request(self):
        if not self._connected:
            raise CdnConnectError("Not connected")
        headers = {k.lower(): v for k, v in self._headers.items()}
        headers.setdefault("host", self._host)
        headers.setdefault("accept", "*/*")
        headers.setdefault("accept-encoding", "identity")
        headers.setdefault("connection", "keep-alive")

        req_line = f"GET {self._path} HTTP/1.1\r\n".encode()
        header_lines = b"".join(f"{k}: {v}\r\n".encode() for k, v in headers.items())
        request = req_line + header_lines + b"\r\n"

        self._writer.write(request)
        async with asyncio.timeout(self._connect_timeout):
            await self._writer.drain()

    async def next_request(self, headers: dict) -> CdnResponse:
        """Send another request on this keep-alive connection.

        Only valid when the previous response body was fully consumed under
        an exact Content-Length framing, leaving the socket precisely at the
        next message boundary. If the server closed meanwhile, the send/read
        raises (connect/protocol/timeout error) and the caller must fall back
        to a fresh connection. Returns the new response headers.
        """
        self._headers = dict(headers)
        await self.send_request()
        return await self.read_response_headers()

    async def read_response_headers(self) -> CdnResponse:
        resp = CdnResponse()

        async with asyncio.timeout(self._read_timeout):
            status_line = await _read_line(self._reader)
            parts = status_line.strip().split(b" ", maxsplit=2)
            if len(parts) < 2:
                raise CdnProtocolError(f"Bad status line: {status_line!r}")
            resp.http_version = parts[0].decode()
            try:
                resp.status_code = int(parts[1])
            except ValueError as e:
                raise CdnProtocolError(f"Bad status code: {parts[1]!r}") from e
            resp.reason = parts[2].decode(errors="replace") if len(parts) > 2 else ""

            while True:
                hline = await _read_line(self._reader)
                if hline == b"":
                    raise CdnProtocolError("Connection closed while reading headers")
                if hline in (b"\r\n", b"\n"):
                    break
                colon = hline.find(b":")
                if colon > 0:
                    k = hline[:colon].decode("latin-1").strip()
                    v = hline[colon + 1 :].strip().decode("latin-1").strip()
                    resp.headers[k.lower()] = v

        self._response = resp
        return resp

    async def iter_chunks(self):
        content_length = self._response.headers.get("content-length")

        if content_length is not None and str(content_length).strip() != "":
            try:
                remaining = int(str(content_length).strip())
            except ValueError as e:
                raise CdnProtocolError(f"Invalid Content-Length: {content_length!r}") from e
            while remaining > 0:
                try:
                    if self._read_timeout:
                        async with asyncio.timeout(self._read_timeout):
                            chunk = await self._reader.read(min(self._read_chunk_size, remaining))
                    else:
                        chunk = await self._reader.read(min(self._read_chunk_size, remaining))
                except TimeoutError as e:
                    raise CdnTimeoutError(f"Read timeout after {remaining} bytes remaining") from e
                if not chunk:
                    raise CdnProtocolError("Upstream connection closed prematurely")
                remaining -= len(chunk)
                yield chunk
            return

        transfer_encoding = self._response.headers.get("transfer-encoding", "").lower()
        if transfer_encoding == "chunked":
            while True:
                async with asyncio.timeout(self._read_timeout):
                    line = await _read_line(self._reader)
                if line == b"":
                    raise CdnProtocolError("Connection closed prematurely before receiving final chunk")
                hex_len = line.strip().split(b";")[0]
                if not hex_len:
                    continue
                try:
                    chunk_size = int(hex_len, 16)
                except ValueError as e:
                    raise CdnProtocolError(f"Bad chunk size: {hex_len!r}") from e
                if chunk_size == 0:
                    async with asyncio.timeout(self._read_timeout):
                        await _read_line(self._reader)
                    break
                remaining = chunk_size
                while remaining > 0:
                    try:
                        if self._read_timeout:
                            async with asyncio.timeout(self._read_timeout):
                                chunk = await self._reader.read(min(self._read_chunk_size, remaining))
                        else:
                            chunk = await self._reader.read(min(self._read_chunk_size, remaining))
                    except TimeoutError as e:
                        raise CdnTimeoutError(f"Read timeout after {remaining} bytes remaining") from e
                    if not chunk:
                        raise CdnProtocolError("Truncated chunked body")
                    remaining -= len(chunk)
                    yield chunk
                async with asyncio.timeout(self._read_timeout):
                    await _read_line(self._reader)
            return

        while True:
            try:
                if self._read_timeout:
                    async with asyncio.timeout(self._read_timeout):
                        chunk = await self._reader.read(self._read_chunk_size)
                else:
                    chunk = await self._reader.read(self._read_chunk_size)
            except TimeoutError as e:
                raise CdnTimeoutError("Read timeout on connection-closed body") from e
            if not chunk:
                break
            yield chunk

    async def read_debug_body(self, max_bytes: int = 2048, timeout: float = 5.0) -> bytes:
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.read(max_bytes)
        except TimeoutError:
            return b""

    async def close(self):
        if self._writer:
            try:
                self._writer.close()
                async with asyncio.timeout(5.0):
                    await self._writer.wait_closed()
            except Exception:
                try:
                    self._writer.transport.abort()
                except Exception:
                    pass
            self._writer = None
            self._reader = None
        self._connected = False
