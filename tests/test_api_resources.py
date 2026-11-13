import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from miku.api import bangumi, comment, live, user, video
from miku.api.exceptions import ArgsException, ResponseCodeException


class TestApiInputValidation(unittest.IsolatedAsyncioTestCase):
    def test_video_and_make_video_validation(self):
        v1 = video.make_video("BV17x411w7KC")
        self.assertEqual(v1.get_aid(), 170001)
        v2 = video.make_video("av170001")
        self.assertEqual(v2.get_bvid(), "BV17x411w7KC")
        v3 = video.make_video("170001")
        self.assertEqual(v3.get_bvid(), "BV17x411w7KC")

        for bad_bvid in (None, 123, "BV17x411w7KC\n", "BVshort"):
            with self.subTest(bad_bvid=bad_bvid):
                with self.assertRaises(ArgsException):
                    video.Video(bvid=bad_bvid)

        for bad_aid in (True, False, 1.5, 0, -5, "1_000", "abc", "av"):
            with self.subTest(bad_aid=bad_aid):
                with self.assertRaises(ArgsException):
                    video.Video(aid=bad_aid)

        for bad_vid in ("", "invalid", "av", 123, True):
            with self.subTest(bad_vid=bad_vid):
                with self.assertRaises(ArgsException):
                    video.make_video(bad_vid)

    async def test_video_cid_index_and_comment_and_user_validation(self):
        v = video.Video(bvid="BV17x411w7KC")
        for bad_idx in (True, False, -1, 1.5, "0"):
            with self.subTest(bad_idx=bad_idx):
                with self.assertRaises(ArgsException):
                    await v.get_cid(bad_idx)

        for bad_uid in (True, False, 1.5, 0, -1, "abc", "1_000", "²"):
            with self.subTest(bad_uid=bad_uid):
                with self.assertRaises(ArgsException):
                    user.User(uid=bad_uid)

        u = user.User(uid="12345")
        self.assertEqual(u.uid, 12345)
        for bad_page in (True, False, 1.5):
            with self.subTest(bad_page=bad_page):
                with self.assertRaises(ArgsException):
                    await u.get_videos(pn=bad_page)
                with self.assertRaises(ArgsException):
                    await u.get_videos(ps=bad_page)
                with self.assertRaises(ArgsException):
                    await u.get_articles(pn=bad_page)
                with self.assertRaises(ArgsException):
                    await u.get_articles(ps=bad_page)

        for bad_oid in (True, False, 1.5, 0, -1, "abc", "1_000"):
            with self.subTest(bad_oid=bad_oid):
                with self.assertRaises(ArgsException):
                    await comment.get_comments(bad_oid, comment.CommentResourceType.VIDEO)

        for bad_pi in (True, False, 0, -1, 1.5, "1"):
            with self.subTest(bad_pi=bad_pi):
                with self.assertRaises(ArgsException):
                    await comment.get_comments(170001, comment.CommentResourceType.VIDEO, page_index=bad_pi)
                with self.assertRaises(ArgsException):
                    await comment.get_sub_comments(170001, 999, page_index=bad_pi)

    def test_bangumi_id_validation(self):
        with self.assertRaises(ArgsException):
            bangumi.Bangumi()
        for kw in (
            {"ssid": True},
            {"ssid": 0},
            {"ssid": -1},
            {"ssid": 1.5},
            {"ssid": "abc"},
            {"media_id": False},
            {"epid": "1_0"},
        ):
            with self.subTest(kw=kw):
                with self.assertRaises(ArgsException):
                    bangumi.Bangumi(**kw)


class TestVideoGaiaRetry(unittest.IsolatedAsyncioTestCase):
    async def test_request_playurl_retries_on_minus_352(self):
        calls: list[dict] = []

        class FakeApi:
            def __init__(self, *args, **kwargs):
                self._params = {}

            def update_params(self, **kwargs):
                self._params = dict(kwargs)
                return self

            @property
            def result(self):
                calls.append(self._params)
                if len(calls) == 1:

                    async def fail():
                        raise ResponseCodeException(-352, "risk control")

                    return fail()

                async def ok():
                    return {"dash": {"video": []}}

                return ok()

        v = video.Video(bvid="BV17x411w7KC")
        with patch.object(video, "Api", FakeApi):
            res = await v._request_playurl(
                {"url": "https://api.bilibili.com/x/player/wbi/playurl", "method": "GET", "wbi": True},
                {"bvid": "BV17x411w7KC", "cid": 123},
                "test_playurl",
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["gaia_source"], "pre-load")
        self.assertEqual(calls[1]["isGaiaAvoided"], "true")
        self.assertEqual(calls[1]["try_look"], 1)
        self.assertEqual(res, {"dash": {"video": []}})


class TestUserDegradedAndBangumiResolve(unittest.IsolatedAsyncioTestCase):
    async def test_user_degraded_does_not_clobber_across_surfaces(self):
        def fake_api_factory(url, **kwargs):
            inst = MagicMock()

            def update_params(**params):
                async def run():
                    if "acc/info" in url:
                        raise ResponseCodeException(-352, "risk")
                    if "/card" in url:
                        return {"card": {"mid": "123", "name": "Miku"}}
                    if "arc/search" in url:
                        return {"list": {"vlist": []}}
                    if "recArchivesByKeywords" in url:
                        return {
                            "archives": [{"aid": 170001, "bvid": "BV17x411w7KC", "title": "Song"}],
                            "page": {"total": 1},
                        }
                    raise AssertionError(f"Unexpected URL: {url}")

                inst.result = run()
                return inst

            inst.update_params = update_params
            return inst

        u = user.User(uid=123)
        with patch.object(user, "Api", side_effect=fake_api_factory):
            info, videos = await asyncio.gather(u.get_user_info(), u.get_videos())

        self.assertEqual(info["name"], "Miku")
        self.assertEqual(len(videos["list"]["vlist"]), 1)
        self.assertTrue(u._info_degraded)
        self.assertFalse(u._videos_degraded)
        self.assertTrue(u._degraded)

    async def test_bangumi_resolve_epid_and_media_id(self):
        seen_calls: list[tuple[str, dict]] = []

        def fake_api_factory(url, **kwargs):
            inst = MagicMock()

            def update_params(**params):
                seen_calls.append((url, dict(params)))

                async def run():
                    if url == bangumi.API["info"]["meta"]["url"]:
                        return {"media": {"media_id": 2822, "season_id": 4455}}
                    if url == bangumi.API["info"]["collective_info"]["url"]:
                        return {"season_id": 4455, "media_id": 2822, "title": "Show"}
                    if url == bangumi.API["info"]["episodes_list"]["url"]:
                        return {"main_section": {"episodes": [{"id": 7788}]}}
                    raise AssertionError(f"Unexpected URL: {url}")

                inst.result = run()
                return inst

            inst.update_params = update_params
            return inst

        with patch.object(bangumi, "Api", side_effect=fake_api_factory):
            b_ep = bangumi.Bangumi(epid=7788)
            eps = await b_ep.get_episode_list()
            self.assertEqual(b_ep.ssid, 4455)
            self.assertEqual(b_ep.media_id, 2822)
            self.assertEqual(eps["main_section"]["episodes"][0]["id"], 7788)

            b_md = bangumi.Bangumi(media_id=2822)
            await b_md._resolve()
            self.assertEqual(b_md.ssid, 4455)

        self.assertEqual(seen_calls[0][1], {"ep_id": 7788})
        self.assertEqual(seen_calls[2][1], {"media_id": 2822})
        self.assertEqual(seen_calls[3][1], {"season_id": 4455})


class TestLiveDanmakuHardening(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_connector_session_retries_and_timeouts(self):
        connectors: list[MagicMock] = []
        sessions: list[MagicMock] = []
        ws_calls: list[tuple[str, object]] = []

        def make_connector(*args, **kwargs):
            c = MagicMock()
            c.close = AsyncMock()
            connectors.append(c)
            return c

        def make_session(*args, **kwargs):
            s = MagicMock()
            s.close = AsyncMock()

            async def ws_connect(uri, **kw):
                ws_calls.append((uri, kw.get("timeout")))
                if len(ws_calls) < 3:
                    raise OSError("connect refused")
                ws = MagicMock()
                ws.closed = False
                ws.send_bytes = AsyncMock()
                ws.close = AsyncMock()
                ws.receive = AsyncMock(return_value=MagicMock(type=aiohttp.WSMsgType.CLOSE))
                return ws

            s.ws_connect = ws_connect
            sessions.append(s)
            return s

        fake_room = MagicMock()
        fake_room.get_room_id = AsyncMock(return_value=999)
        fake_room.get_danmu_info = AsyncMock(
            return_value={
                "token": "tok",
                "host_list": [
                    {"host": "h1.example.com", "wss_port": 443},
                    {"host": "h2.example.com", "wss_port": 443},
                ],
            }
        )

        dm = live.LiveDanmaku(123, max_retry=1, retry_after=0.01)
        with (
            patch.object(live, "LiveRoom", return_value=fake_room),
            patch("aiohttp.TCPConnector", side_effect=make_connector),
            patch("aiohttp.ClientSession", side_effect=make_session),
        ):
            await dm.connect()

        self.assertEqual(len(connectors), 1)
        self.assertEqual(len(sessions), 1)
        # Round 1: h1 + h2 fail; Round 2: h1 succeeds.
        self.assertEqual(len(ws_calls), 3)
        self.assertIsInstance(ws_calls[0][1], aiohttp.ClientWSTimeout)
        connectors[0].close.assert_awaited_once()
        sessions[0].close.assert_awaited_once()

    async def test_heartbeat_closes_ws_on_timeout(self):
        dm = live.LiveDanmaku(123)
        ws = MagicMock()
        ws.closed = False
        ws.send_bytes = AsyncMock(side_effect=TimeoutError("send timed out"))
        ws.close = AsyncMock()
        dm._ws = ws

        await dm._heartbeat()
        ws.close.assert_awaited_once()

    async def test_dispatch_logs_sync_and_async_exceptions(self):
        dm = live.LiveDanmaku(123)

        @dm.on("TEST")
        def sync_bad(event):
            raise RuntimeError("sync boom")

        @dm.on("TEST")
        async def async_bad(event):
            raise RuntimeError("async boom")

        with self.assertLogs(dm.logger, level="ERROR") as cm:
            dm.dispatch("TEST", {})
            await asyncio.sleep(0.02)

        joined = "\n".join(cm.output)
        self.assertIn("sync boom", joined)
        self.assertIn("async boom", joined)
        self.assertEqual(len(dm._tasks), 0)

    def test_unpack_bad_verify_json_and_coerce_uid(self):
        dm = live.LiveDanmaku(123)
        bad_payload = b"{not-json"
        packet = (
            struct.pack(">IHHII", 16 + len(bad_payload), 16, 0, dm.DATAPACK_TYPE_VERIFY_SUCCESS_RESPONSE, 1)
            + bad_payload
        )
        unpacked = dm._unpack(packet)
        self.assertEqual(len(unpacked), 1)
        self.assertIsNone(unpacked[0]["data"])

        self.assertEqual(dm._coerce_uid("456"), 456)
        self.assertEqual(dm._coerce_uid(True), 0)
        self.assertEqual(dm._coerce_uid("bad"), 0)
        self.assertEqual(dm._coerce_uid(None), 0)


if __name__ == "__main__":
    unittest.main()
