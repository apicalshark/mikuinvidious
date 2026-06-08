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
    writer.write(b"\x05\x01\x00")
    await asyncio.wait_for(writer.drain(), timeout=timeout)
    resp = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
    if resp[0] != 5:
        raise CdnConnectError(f"SOCKS5: bad version {resp[0]}")
    if resp[1] != 0:
        raise CdnConnectError(f"SOCKS5: auth method {resp[1]} required")

    target_bytes = target_host.encode("idna")
    msg = b"\x05\x01\x00\x03" + bytes([len(target_bytes)]) + target_bytes + target_port.to_bytes(2, "big")
    writer.write(msg)
    await asyncio.wait_for(writer.drain(), timeout=timeout)
    resp = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
    if resp[0] != 5:
        raise CdnConnectError(f"SOCKS5: bad reply version {resp[0]}")
    if resp[1] != 0:
        raise CdnConnectError(f"SOCKS5: connect failed status {resp[1]}")
    atype = resp[3]
    if atype == 1:
        await asyncio.wait_for(reader.readexactly(6), timeout=timeout)
    elif atype == 3:
        dlen = (await asyncio.wait_for(reader.readexactly(1), timeout=timeout))[0]
        await asyncio.wait_for(reader.readexactly(dlen + 2), timeout=timeout)
    elif atype == 4:
        await asyncio.wait_for(reader.readexactly(18), timeout=timeout)
    else:
        raise CdnConnectError(f"SOCKS5: unknown address type {atype}")


async def _http_connect_tunnel(writer, reader, target_host, target_port, timeout):
    req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    writer.write(req)
    await asyncio.wait_for(writer.drain(), timeout=timeout)
    line = await asyncio.wait_for(_read_line(reader), timeout=timeout)
    try:
        status = int(line.split(b" ")[1])
    except (ValueError, IndexError):
        raise CdnProtocolError(f"Bad HTTP CONNECT status line: {line!r}")
    if status != 200:
        raise CdnConnectError(f"HTTP CONNECT failed: {line.decode(errors='replace').strip()}")
    while True:
        hline = await asyncio.wait_for(_read_line(reader), timeout=timeout)
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
        read_chunk_size: int = 64 * 1024,
        connect_timeout: float = 15.0,
        read_timeout: float = 30.0,
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
        if self._proxy_url:
            proxy_scheme, proxy_host, proxy_port = _parse_proxy_url(self._proxy_url)
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(proxy_host, proxy_port),
                timeout=self._connect_timeout,
            )
            if proxy_scheme.startswith("socks"):
                await _socks5_handshake(self._writer, self._reader, self._host, self._port, self._connect_timeout)
            elif proxy_scheme == "http":
                await _http_connect_tunnel(self._writer, self._reader, self._host, self._port, self._connect_timeout)
            else:
                raise CdnConnectError(f"Unsupported proxy scheme: {proxy_scheme}")

            if self._use_tls:
                ctx = ssl.create_default_context()
                transport, protocol = await asyncio.wait_for(
                    asyncio.start_tls(
                        transport=self._writer.transport,
                        protocol=self._writer.transport.get_protocol(),
                        sslcontext=ctx,
                        server_hostname=self._host,
                    ),
                    timeout=self._connect_timeout,
                )
                self._writer = asyncio.StreamWriter(transport, protocol, self._reader, asyncio.get_running_loop())
        else:
            ctx = ssl.create_default_context() if self._use_tls else None
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port, ssl=ctx, server_hostname=self._host if ctx else None),
                timeout=self._connect_timeout,
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
        header_lines = b"".join(
            f"{k}: {v}\r\n".encode() for k, v in headers.items()
        )
        request = req_line + header_lines + b"\r\n"

        self._writer.write(request)
        await asyncio.wait_for(self._writer.drain(), timeout=self._connect_timeout)

    async def read_response_headers(self) -> CdnResponse:
        resp = CdnResponse()

        status_line = await asyncio.wait_for(_read_line(self._reader), timeout=self._read_timeout)
        parts = status_line.strip().split(b" ", maxsplit=2)
        if len(parts) < 2:
            raise CdnProtocolError(f"Bad status line: {status_line!r}")
        resp.http_version = parts[0].decode()
        try:
            resp.status_code = int(parts[1])
        except ValueError:
            raise CdnProtocolError(f"Bad status code: {parts[1]!r}")
        resp.reason = parts[2].decode(errors="replace") if len(parts) > 2 else ""

        while True:
            hline = await asyncio.wait_for(_read_line(self._reader), timeout=self._read_timeout)
            if hline == b"":
                raise CdnProtocolError("Connection closed while reading headers")
            if hline in (b"\r\n", b"\n"):
                break
            colon = hline.find(b":")
            if colon > 0:
                k = hline[:colon].decode("latin-1").strip()
                v = hline[colon + 1:].strip().decode("latin-1").strip()
                resp.headers[k.lower()] = v

        self._response = resp
        return resp

    async def iter_chunks(self):
        content_length = self._response.headers.get("content-length")

        if content_length:
            try:
                remaining = int(content_length)
            except ValueError:
                raise CdnProtocolError(f"Invalid Content-Length: {content_length!r}")
            while remaining > 0:
                try:
                    chunk = await asyncio.wait_for(
                        self._reader.read(min(self._read_chunk_size, remaining)),
                        timeout=self._read_timeout,
                    )
                except asyncio.TimeoutError:
                    raise CdnTimeoutError(f"Read timeout after {remaining} bytes remaining")
                if not chunk:
                    raise CdnProtocolError("Upstream connection closed prematurely")
                remaining -= len(chunk)
                yield chunk
            return

        transfer_encoding = self._response.headers.get("transfer-encoding", "").lower()
        if transfer_encoding == "chunked":
            while True:
                line = await asyncio.wait_for(_read_line(self._reader), timeout=self._read_timeout)
                if line == b"":
                    raise CdnProtocolError("Connection closed prematurely before receiving final chunk")
                hex_len = line.strip().split(b";")[0]
                if not hex_len:
                    continue
                try:
                    chunk_size = int(hex_len, 16)
                except ValueError:
                    raise CdnProtocolError(f"Bad chunk size: {hex_len!r}")
                if chunk_size == 0:
                    await asyncio.wait_for(_read_line(self._reader), timeout=self._read_timeout)
                    break
                remaining = chunk_size
                while remaining > 0:
                    chunk = await asyncio.wait_for(
                        self._reader.read(min(self._read_chunk_size, remaining)),
                        timeout=self._read_timeout,
                    )
                    if not chunk:
                        raise CdnProtocolError("Truncated chunked body")
                    remaining -= len(chunk)
                    yield chunk
                await asyncio.wait_for(_read_line(self._reader), timeout=self._read_timeout)
            return

        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(self._read_chunk_size),
                    timeout=self._read_timeout,
                )
            except asyncio.TimeoutError:
                raise CdnTimeoutError("Read timeout on connection-closed body")
            if not chunk:
                break
            yield chunk

    async def read_debug_body(self, max_bytes: int = 2048, timeout: float = 5.0) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.read(max_bytes), timeout=timeout)
        except asyncio.TimeoutError:
            return b""

    async def close(self):
        if self._writer:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=5.0)
            except Exception:
                try:
                    self._writer.transport.abort()
                except Exception:
                    pass
            self._writer = None
            self._reader = None
            self._connected = False
