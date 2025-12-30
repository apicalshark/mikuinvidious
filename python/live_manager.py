import asyncio
import time
from collections import deque

from shared import Network


class LiveStream:
    def __init__(self, url, headers, cookies):
        self.url = url
        self.headers = headers
        self.cookies = cookies
        self.clients = {}  # client_id -> Queue
        self.chunk_buffer = deque(maxlen=100)  # Increased buffer (approx 1.5MB)
        self.flv_header = b""
        self.metadata_tag = b""
        self.video_seq_header = b""
        self.audio_seq_header = b""
        self.is_running = False
        self.task = None
        self.last_active = time.time()
        self.status_code = None
        self.resp_headers = {}
        self.header_ready = asyncio.Event()
        self.client_joined = asyncio.Event()

        # ML / PERFORMANCE METRICS
        self.metrics = {
            "bytes_received": 0,
            "chunks_received": 0,
            "error_count": 0,
            "reconnect_count": 0,
            "start_time": time.time(),
            "last_chunk_time": time.time(),
            "avg_chunk_size": 0.0,
        }

    def dump_state(self):
        """Export current stream state for JAX/Flax ML processing."""
        duration = time.time() - self.metrics["start_time"]
        bitrate = (self.metrics["bytes_received"] * 8) / max(duration, 1)

        return {
            "stream_info": {
                "url_hash": hash(self.url),
                "is_running": self.is_running,
                "client_count": len(self.clients),
            },
            "performance": {
                "bitrate_bps": bitrate,
                "avg_chunk_size": self.metrics["avg_chunk_size"],
                "total_errors": self.metrics["error_count"],
                "reconnects": self.metrics["reconnect_count"],
            },
            "timestamp": time.time(),
        }

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        print(f"[LiveManager] Starting stream task for: {self.url[:50]}...")
        self.task = asyncio.create_task(self._stream_loop())

    def _parse_flv_tags(self, data):
        """Refined FLV parser to extract exact headers and tags."""
        try:
            # 1. FLV Header & PreviousTagSize0 (13 bytes total)
            if not self.flv_header and b"FLV" in data:
                idx = data.find(b"FLV")
                if len(data) >= idx + 13:
                    self.flv_header = data[idx : idx + 13]
                    print(f"[LiveManager] Found FLV Header for {self.url[:30]}...")

            # 2. Metadata Tag (Type 18)
            if not self.metadata_tag and b"onMetaData" in data:
                idx = data.find(b"onMetaData")
                # Look back for the tag start (Type 18)
                for i in range(idx - 1, max(-1, idx - 32), -1):
                    if data[i] == 0x12:
                        size = int.from_bytes(data[i + 1 : i + 4], "big")
                        if len(data) >= i + 11 + size + 4:
                            self.metadata_tag = data[i : i + 11 + size + 4]
                            print(f"[LiveManager] Found Metadata Tag for {self.url[:30]}...")
                            break

            # 3. Video Sequence Header (Type 9, FrameType 1, Codec 7, AVCPacketType 0)
            if not self.video_seq_header and b"\x17\x00\x00\x00\x00" in data:
                idx = data.find(b"\x17\x00\x00\x00\x00")
                if idx >= 11 and data[idx - 11] == 0x09:
                    size = int.from_bytes(data[idx - 10 : idx - 7], "big")
                    if len(data) >= idx - 11 + 11 + size + 4:
                        self.video_seq_header = data[idx - 11 : idx - 11 + 11 + size + 4]
                        print(f"[LiveManager] Found Video Seq Header for {self.url[:30]}...")

            # 4. Audio Sequence Header (Type 8, SoundFormat 10, AACPacketType 0)
            if not self.audio_seq_header and b"\xaf\x00" in data:
                idx = data.find(b"\xaf\x00")
                if idx >= 11 and data[idx - 11] == 0x08:
                    size = int.from_bytes(data[idx - 10 : idx - 7], "big")
                    if len(data) >= idx - 11 + 11 + size + 4:
                        self.audio_seq_header = data[idx - 11 : idx - 11 + 11 + size + 4]
                        print(f"[LiveManager] Found Audio Seq Header for {self.url[:30]}...")
        except Exception:
            pass

    async def aclose(self):
        """Forcefully close the stream and cleanup."""
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        print(f"[LiveManager] Stream {self.url[:30]}... closed via aclose()")

    async def _stream_loop(self):
        retry_count = 0
        max_retries = 5

        while self.is_running:
            try:
                # Wait for clients if none
                if not self.clients:
                    self.header_ready.clear()
                    self.resp_headers = {}
                    try:
                        # Wait for a client to join or timeout
                        await asyncio.wait_for(self.client_joined.wait(), timeout=5.0)
                        self.client_joined.clear()
                    except asyncio.TimeoutError:
                        if not self.clients:
                            print(f"[LiveManager] No clients for 5s, self-destructing: {self.url[:50]}")
                            self.is_running = False
                            break

                if not self.is_running:
                    break

                client = await Network.get_async_client()
                async with client.stream(
                    "GET", self.url, headers=self.headers, cookies=self.cookies, follow_redirects=True
                ) as resp:
                    self.status_code = resp.status_code
                    self.resp_headers = dict(resp.headers)

                    if self.status_code >= 400:
                        print(f"[LiveManager] Origin returned {self.status_code}: {self.url[:50]}")
                        self.header_ready.set()
                        retry_count += 1
                        if retry_count >= max_retries:
                            self.is_running = False
                            break
                        await asyncio.sleep(5)
                        continue

                    print(f"[LiveManager] Stream connected (Attempt {retry_count + 1}): {self.url[:50]}")
                    retry_count = 0  # Reset on success

                    last_signal_time = time.time()
                    resp_iter = resp.aiter_bytes(chunk_size=16384)

                    while self.is_running:
                        try:
                            # Short timeout to allow periodically checking for clients and is_running
                            chunk = await asyncio.wait_for(anext(resp_iter), timeout=5.0)
                        except StopAsyncIteration:
                            print(f"[LiveManager] Upstream reached EOF: {self.url[:50]}")
                            self.is_running = False
                            return 
                        except asyncio.TimeoutError:
                            # No data from upstream for 5s. Check if anyone is still watching.
                            if not self.clients and (time.time() - last_signal_time > 5.0):
                                print("[LiveManager] No clients for 5s during silence. Killing upstream.")
                                self.is_running = False
                                return
                            continue

                        # Update last signal time if we have clients
                        if self.clients:
                            last_signal_time = time.time()
                        elif time.time() - last_signal_time > 5.0:
                            print("[LiveManager] No clients for 5s. Killing upstream.")
                            self.is_running = False
                            return

                        # Process & Broadcast
                        self.metrics["bytes_received"] += len(chunk)
                        self.metrics["last_chunk_time"] = time.time()
                        self._parse_flv_tags(chunk)
                        self.chunk_buffer.append(chunk)

                        if not self.header_ready.is_set():
                            self.header_ready.set()

                        disconnected_client_ids = []
                        for cid, q in list(self.clients.items()):
                            try:
                                q.put_nowait(chunk)
                            except asyncio.QueueFull:
                                while not q.empty():
                                    q.get_nowait()
                                self._send_burst(q, send_flv_header=False)
                            except Exception:
                                disconnected_client_ids.append(cid)

                        for cid in disconnected_client_ids:
                            self.clients.pop(cid, None)

                        self.last_active = time.time()

            except asyncio.CancelledError:
                self.is_running = False
                raise
            except Exception as e:
                retry_count += 1
                print(f"[LiveManager] Connection error (Retry {retry_count}/{max_retries}): {e}")
                if retry_count >= max_retries:
                    self.is_running = False
                    break
                await asyncio.sleep(min(retry_count * 2, 10))

        print(f"[LiveManager] Stream task terminating: {self.url[:50]}")
        self.is_running = False
        self.header_ready.set()
        for q in list(self.clients.values()):
            try:
                q.put_nowait(None)
            except Exception:
                pass
        self.clients.clear()

    def _send_burst(self, q, send_flv_header=True):
        """Sends the latest headers and buffer to a client."""
        try:
            # Send FLV signature first (Only for NEW clients)
            if send_flv_header and self.flv_header:
                q.put_nowait(self.flv_header)
            # Send metadata and sequence headers (Video/Audio Config)
            for h in [self.metadata_tag, self.video_seq_header, self.audio_seq_header]:
                if h:
                    q.put_nowait(h)
            # Send the recent chunks
            for chunk in list(self.chunk_buffer):
                q.put_nowait(chunk)
        except asyncio.QueueFull:
            print("[LiveManager] Burst delivery failed: queue full again.")

    def add_client(self, client_id):
        self.last_active = time.time()
        q = asyncio.Queue(maxsize=256)  # Increased queue size
        self._send_burst(q, send_flv_header=True)
        self.clients[client_id] = q
        print(f"[LiveManager] Client connected: {client_id}. Total clients: {len(self.clients)}")
        self.client_joined.set()  # Wake up the loop
        return q

    def remove_client(self, client_id, reason="Unknown"):
        if client_id in self.clients:
            q = self.clients.pop(client_id)
            # If the queue is waiting for data, waking it up with None
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                # Clear queue and force None to ensure generator exits
                while not q.empty():
                    try:
                        q.get_nowait()
                    except Exception:
                        break
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            except Exception:
                pass
            print(
                f"[LiveManager] Client disconnected: {client_id} (Reason: {reason}). Total clients: {len(self.clients)}"
            )
        self.last_active = time.time()


class LiveStreamManager:
    def __init__(self):
        self.streams = {}  # url -> LiveStream
        self.lock = asyncio.Lock()
        self._cleanup_task = None

    async def _cleanup_loop(self):
        """Periodically remove inactive streams from the manager."""
        while True:
            await asyncio.sleep(60)
            async with self.lock:
                to_remove = [url for url, s in self.streams.items() if not s.is_running]
                for url in to_remove:
                    print(f"[LiveManager] Cleaning up stale stream: {url[:50]}")
                    s = self.streams.pop(url)
                    await s.aclose()

    async def subscribe(self, url, headers, cookies, client_id):
        async with self.lock:
            if self._cleanup_task is None:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())

            if url not in self.streams or not self.streams[url].is_running:
                print(f"[LiveManager] Creating new LiveStream instance for: {url[:50]}...")
                stream = LiveStream(url, headers, cookies)
                self.streams[url] = stream
                await stream.start()

        stream = self.streams[url]

        # Add client FIRST to wake up the stream loop if it's waiting
        print(f"[LiveManager] Subscribing client to: {url[:50]}")
        q = stream.add_client(client_id)

        try:
            # Efficiently wait for headers from the now-active stream
            print(f"[LiveManager] Waiting for headers from upstream: {url[:50]}...")
            await asyncio.wait_for(stream.header_ready.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            print(f"[LiveManager] Timeout waiting for headers (Returning partial stream): {url[:50]}")

        if not stream.is_running and not stream.chunk_buffer:
            print("[LiveManager] Subscription failed: Stream not running and buffer empty.")
            # Clean up the client if subscription failed
            stream.remove_client(client_id, reason="Subscription timeout/failure")
            return stream, None

        return stream, q


live_manager = LiveStreamManager()
