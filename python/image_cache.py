import asyncio
import hashlib
import os
import time
import uuid

import aiofiles


class ImageCache:
    """High-performance local disk cache for proxied images.

    Stores images on disk to eliminate upstream Bilibili CDN fetches on repeat views.
    Protects against state-actor scrapers, traffic floods, and CDN IP rate limits.
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        max_size_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ):
        if cache_dir is None:
            cache_dir = os.environ.get("IMAGE_CACHE_DIR", os.path.join("data", "img_cache"))
        self.cache_dir = os.path.abspath(cache_dir)

        if max_size_bytes is None:
            max_mb = int(os.environ.get("IMAGE_CACHE_MAX_SIZE_MB", 2048))
            max_size_bytes = max_mb * 1024 * 1024
        self.max_size_bytes = max_size_bytes

        if ttl_seconds is None:
            ttl_days = int(os.environ.get("IMAGE_CACHE_TTL_DAYS", 7))
            ttl_seconds = ttl_days * 86400
        self.ttl_seconds = ttl_seconds

        self._op_counter = 0
        self._prune_lock = asyncio.Lock()
        os.makedirs(self.cache_dir, exist_ok=True)

    def _url_to_key(self, url: str) -> str:
        """Derive a safe filesystem key from a URL using SHA-256."""
        return hashlib.sha256(url.encode()).hexdigest()

    def _get_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.cache")

    async def get(self, url: str) -> tuple[bytes, str] | None:
        """Retrieve cached image content and content-type.

        Returns (content, content_type) on hit, or None on miss or expiration.
        """
        key = self._url_to_key(url)
        path = self._get_path(key)

        if not os.path.exists(path):
            return None

        try:
            async with aiofiles.open(path, "rb") as f:
                raw = await f.read()

            parts = raw.split(b"\n\n", 1)
            if len(parts) != 2:
                # Corrupted file
                os.remove(path)
                return None

            header, body = parts
            header_lines = header.split(b"\n")
            if len(header_lines) < 2:
                os.remove(path)
                return None

            content_type = header_lines[0].decode("utf-8", errors="replace")
            created_at = int(header_lines[1].decode("ascii", errors="replace"))

            # Check expiration
            if time.time() - created_at > self.ttl_seconds:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None

            # Touch atime for LRU tracking
            try:
                now = time.time()
                os.utime(path, (now, now))
            except OSError:
                pass

            return body, content_type
        except Exception:
            # On any I/O or decode failure, treat as cache miss and try removing broken file
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            return None

    async def set(self, url: str, content: bytes, content_type: str) -> None:
        """Atomically cache an image with its content-type and creation timestamp."""
        key = self._url_to_key(url)
        target_path = self._get_path(key)
        tmp_path = os.path.join(self.cache_dir, f"{key}.{uuid.uuid4().hex}.tmp")

        header = f"{content_type}\n{int(time.time())}\n\n".encode()
        payload = header + content

        try:
            async with aiofiles.open(tmp_path, "wb") as f:
                await f.write(payload)

            os.replace(tmp_path, target_path)

            self._op_counter += 1
            if self._op_counter >= 100:
                self._op_counter = 0
                asyncio.create_task(self.prune_if_needed())
        except Exception as e:
            # Cache failure must never crash request handling (fail-open)
            print(f"[ImageCache] Failed to write cache for {url}: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    async def prune_if_needed(self) -> None:
        """Prune oldest cache files if total disk usage exceeds max_size_bytes."""
        async with self._prune_lock:
            try:
                entries = []
                total_size = 0

                for entry in os.scandir(self.cache_dir):
                    if entry.is_file() and entry.name.endswith(".cache"):
                        stat = entry.stat()
                        total_size += stat.st_size
                        # Use atime or mtime for LRU ordering
                        entries.append((stat.st_atime, stat.st_size, entry.path))

                if total_size <= self.max_size_bytes:
                    return

                # Target 80% of max_size_bytes
                target_size = int(self.max_size_bytes * 0.8)
                entries.sort(key=lambda x: x[0])  # oldest first

                for _timestamp, size, file_path in entries:
                    try:
                        os.remove(file_path)
                        total_size -= size
                        if total_size <= target_size:
                            break
                    except OSError:
                        pass
            except Exception as e:
                print(f"[ImageCache] Pruning error: {e}")


# Global default image cache instance
image_cache = ImageCache()
