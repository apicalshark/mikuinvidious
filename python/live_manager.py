import asyncio
import httpx
import time
from collections import deque
from shared import Network

class LiveStream:
    def __init__(self, url, headers, cookies):
        self.url = url
        self.headers = headers
        self.cookies = cookies
        self.clients = set()
        self.chunk_buffer = deque(maxlen=30) # Approx 2MB buffer
        self.flv_header = b"" # FLV 9-byte header + PreviousTagSize0
        self.metadata_tag = b"" # First script data tag
        self.video_seq_header = b"" # AVC sequence header
        self.audio_seq_header = b"" # AAC sequence header
        self.is_running = False
        self.task = None
        self.last_active = time.time()
        self.status_code = None
        self.resp_headers = {}

    async def start(self):
        if self.is_running: return
        self.is_running = True
        self.task = asyncio.create_task(self._stream_loop())

    def _parse_flv_tags(self, data):
        """Minimal FLV parser to keep headers fresh."""
        try:
            if data.startswith(b'FLV'):
                self.flv_header = data[:13]
                return

            # AVC Sequence Header usually contains this sequence
            idx = data.find(b'\x17\x00\x00\x00\x00') 
            if idx != -1 and idx > 5:
                self.video_seq_header = data[max(0, idx-11):idx+256]
            
            # AAC Sequence Header usually contains 0xAF 0x00
            idx = data.find(b'\xaf\x00')
            if idx != -1 and idx > 5:
                self.audio_seq_header = data[max(0, idx-11):idx+64]

            # Script Tag (Metadata)
            if b'onMetaData' in data:
                idx = data.find(b'\x12') # Tag type 18
                if idx != -1:
                    self.metadata_tag = data[idx:idx+1024]
        except: pass

    async def _stream_loop(self):
        reconnect_delay = 1
        while self.is_running:
            try:
                client = Network.get_async_client()
                async with client.stream("GET", self.url, headers=self.headers, cookies=self.cookies) as resp:
                    self.status_code = resp.status_code
                    self.resp_headers = dict(resp.headers)
                    
                    if self.status_code >= 400:
                        print(f"[LiveManager] Stream error: {self.status_code} for {self.url[:50]}")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 30)
                        continue

                    reconnect_delay = 1
                    print(f"[LiveManager] Started shared stream: {self.url[:50]}")
                    
                    self.flv_header = b""
                    self.metadata_tag = b""
                    self.video_seq_header = b""
                    self.audio_seq_header = b""
                    self.chunk_buffer.clear()
                    
                    async for chunk in resp.aiter_bytes(chunk_size=1024*64):
                        if not self.is_running: break
                        
                        self._parse_flv_tags(chunk)
                        self.chunk_buffer.append(chunk)
                        
                        # Broadcast
                        if self.clients:
                            disconnected_clients = []
                            for q in self.clients:
                                try:
                                    q.put_nowait(chunk)
                                except asyncio.QueueFull:
                                    # Lagging client! "Teleport" them to live edge
                                    while not q.empty():
                                        try: q.get_nowait()
                                        except: break
                                    self._send_burst(q)
                                except Exception:
                                    disconnected_clients.append(q)
                            
                            for q in disconnected_clients:
                                self.clients.discard(q)
                        
                        # Activity check
                        if not self.clients:
                            if time.time() - self.last_active > 10: # 10s grace period
                                print(f"[LiveManager] Idle timeout for {self.url[:50]}...")
                                self.is_running = False
                                break
                        else:
                            self.last_active = time.time()
            except Exception as e:
                print(f"[LiveManager] Stream loop exception: {e}")
                if not self.is_running: break
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)
            
            if not self.is_running: break

        print(f"[LiveManager] Shared stream stopped: {self.url[:50]}...")
        # Notify all remaining clients
        for q in self.clients:
            try: q.put_nowait(None)
            except: pass
        self.clients.clear()

    def _send_burst(self, q):
        """Sends the latest headers and buffer to a client."""
        try:
            if self.flv_header: q.put_nowait(self.flv_header)
            if self.metadata_tag: q.put_nowait(self.metadata_tag)
            if self.video_seq_header: q.put_nowait(self.video_seq_header)
            if self.audio_seq_header: q.put_nowait(self.audio_seq_header)
            for chunk in self.chunk_buffer:
                q.put_nowait(chunk)
        except asyncio.QueueFull: pass

    def add_client(self):
        self.last_active = time.time()
        q = asyncio.Queue(maxsize=100)
        self._send_burst(q)
        self.clients.add(q)
        return q

    def remove_client(self, q):
        self.clients.discard(q)
        self.last_active = time.time()
        print(f"[LiveManager] Client disconnected. Remaining: {len(self.clients)} for {self.url[:50]}")

class LiveStreamManager:
    def __init__(self):
        self.streams = {} # url -> LiveStream
        self.lock = asyncio.Lock()

    async def subscribe(self, url, headers, cookies):
        async with self.lock:
            if url not in self.streams or not self.streams[url].is_running:
                stream = LiveStream(url, headers, cookies)
                self.streams[url] = stream
                await stream.start()
        
        stream = self.streams[url]
        # Wait for headers if they are not yet available (for all clients)
        for _ in range(100): # Up to 10 seconds
            if stream.resp_headers or not stream.is_running:
                break
            await asyncio.sleep(0.1)
        
        if not stream.resp_headers:
            # If still no headers, it might have failed
            if not stream.is_running:
                status = stream.status_code or 502
                return stream, None # Indicate failure
            else:
                # Still running but no headers, maybe very slow
                pass
            
        return stream, stream.add_client()

live_manager = LiveStreamManager()
