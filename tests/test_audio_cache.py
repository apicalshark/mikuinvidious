import asyncio
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.views.audio as audio
from miku.app import app
from redis.asyncio import Redis
from redis.exceptions import ResponseError


@unittest.skipUnless(shutil.which("redis-server"), "redis-server required")
class AudioCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sock = f"{self.tmp.name}/redis.sock"
        self.proc = subprocess.Popen(
            ["redis-server", "--port", "0", "--unixsocket", self.sock, "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.redis = Redis(unix_socket_path=self.sock)

        for _ in range(100):
            try:
                await self.redis.ping()
                break
            except OSError:
                await asyncio.sleep(0.01)
        else:
            self.fail("Redis did not start")

    async def asyncTearDown(self):
        await self.redis.aclose()
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.tmp.cleanup()

    async def test_pair_write_failure_preserves_old_pair_or_neither(self):
        await self.redis.execute_command(
            "ACL", "SETUSER", "writer", "on", "nopass", "~*", "+eval", "+setex", "+expire", "-mset"
        )
        writer = Redis(unix_socket_path=self.sock, username="writer")
        try:
            for denied in ("mset", "expire"):
                await self.redis.execute_command("ACL", "SETUSER", "writer", "+mset", "+expire", f"-{denied}")
                for old in (False, True):
                    with self.subTest(denied=denied, old=old):
                        await self.redis.delete("url", "song")
                        if old:
                            await self.redis.mset({"url": "old-url", "song": "old-song"})
                            await self.redis.expire("url", 900)
                            await self.redis.expire("song", 900)
                        with self.assertRaises(ResponseError):
                            await writer.eval(audio._AUDIO_PAIR_SET, 2, "url", "song", "new-url", "new-song")
                        self.assertEqual(
                            await self.redis.mget("url", "song"),
                            [b"old-url", b"old-song"] if old else [None, None],
                        )
                        if old:
                            self.assertGreater(await self.redis.ttl("url"), 890)
                            self.assertGreater(await self.redis.ttl("song"), 890)
        finally:
            await writer.aclose()
        await self.redis.eval(audio._AUDIO_PAIR_SET, 2, "url", "song", "new-url", "new-song")
        self.assertEqual(await self.redis.mget("url", "song"), [b"new-url", b"new-song"])
        for key in ("url", "song"):
            self.assertGreater(await self.redis.ttl(key), 1790)

    async def test_failed_download_cleanup_preserves_newer_pair(self):
        proxy, song = "mikuinv_am123_0_0", "mikuinv_am123_0_0_song"
        for changed in (False, True):
            with self.subTest(changed=changed):
                await self.redis.mset({proxy: "old-url", song: "100"})

                async def download(changed=changed):
                    if changed:
                        await self.redis.eval(audio._AUDIO_PAIR_SET, 2, proxy, song, "new-url", "300")
                    raise RuntimeError("download failed")

                track = MagicMock()
                track.get_info = AsyncMock(return_value={"title": "Song"})
                track.get_download_url = download
                playlist = MagicMock()
                playlist.get_song_list = AsyncMock(return_value={"data": [{"id": 200}]})
                playlist.get_info = AsyncMock(return_value={})
                with (
                    patch.object(audio, "appredis", self.redis),
                    patch.object(audio, "cache_minutes", return_value=0),
                    patch.object(audio.audio, "Audio", return_value=track),
                    patch.object(audio.audio, "AudioList", return_value=playlist),
                    patch.object(audio, "_fetch_audio_comments_safe", AsyncMock(return_value={})),
                    patch.object(audio, "render_template_with_theme", AsyncMock(return_value="ok")),
                ):
                    async with app.test_request_context("/audio_list/am123"):
                        await audio.audio_list_view("am123")
                self.assertEqual(await self.redis.mget(proxy, song), [b"new-url", b"300"] if changed else [None, None])
