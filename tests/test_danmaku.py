import unittest
from xml.dom import minidom

from miku.media.danmaku import danmaku_xml_conv, parse_danmaku


class TestDanmakuParser(unittest.TestCase):
    def setUp(self):
        self.sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
<i>
    <chatserver>chat.bilibili.com</chatserver>
    <chatid>123456</chatid>
    <d p="12.34,1,25,16777215,1600000000,0,abcd,12345">Standard RTL</d>
    <d p="5.67,4,18,0,1600000000,0,abcd,12346">Bottom Black</d>
    <d p="8.90,5,25,16711680,1600000000,0,abcd,12347">Top Red</d>
    <d p="15.00,6,25,65280,1600000000,0,abcd,12348">LTR Green</d>
    <d p="20.50,7,25,255,1600000000,0,abcd,12349">Special Mode 7</d>
    <d p="25.50,8,25,16776960,1600000000,0,abcd,12350">Special Mode 8</d>
    <d p="30.00,2,25,16777215,1600000000,0,abcd,12351">Invalid Mode 2</d>
    <d p="35.00,1,25,16777215,1600000000,0,abcd,12352">&lt;tag&gt; &amp; &quot;quote&quot;</d>
</i>"""

    def test_parse_danmaku_matches_minidom(self):
        dom = minidom.parseString(self.sample_xml)
        expected = danmaku_xml_conv(dom)
        actual = parse_danmaku(self.sample_xml)

        self.assertEqual(len(actual), len(expected))
        for exp, act in zip(expected, actual, strict=True):
            self.assertEqual(act["text"], exp["text"])
            self.assertEqual(act["mode"], exp["mode"])
            self.assertAlmostEqual(act["time"], exp["time"], places=2)
            self.assertEqual(act["style"], exp["style"])

    def test_parse_danmaku_black_color_styling(self):
        xml = '<d p="1.0,4,20,0,1600000000">Black Text</d>'
        items = parse_danmaku(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["style"]["color"], "#000000")
        self.assertEqual(items[0]["style"]["strokeStyle"], "#fff")
        self.assertIn("-1px -1px #fff", items[0]["style"]["textShadow"])

    def test_parse_danmaku_malformed_truncated_xml(self):
        truncated_xml = """<i>
            <d p="1.0,1,25,16777215,1600000000">Valid Danmaku 1</d>
            <d p="2.0,1,25,16777215,1600000000">Valid Danmaku 2</d>
            <d p="3.0,1,25,16777215,1600000000">Truncated Danm
        """
        # Should not raise exception, must recover valid items
        items = parse_danmaku(truncated_xml)
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(items[0]["text"], "Valid Danmaku 1")
        self.assertEqual(items[1]["text"], "Valid Danmaku 2")

    def test_parse_danmaku_empty_and_invalid(self):
        self.assertEqual(parse_danmaku(""), [])
        self.assertEqual(parse_danmaku(None), [])
        self.assertEqual(parse_danmaku("not xml at all"), [])

    def test_parse_danmaku_mode_7_json_array(self):
        xml = '<d p="55.70,7,25,16777214,1732762088,1,fa754e03,1725292457115742464,10">[0.95,0.2,&quot;1-1&quot;,1.8,&quot;求\\n你\\n了&quot;,0,0,0.95,0.2,500,0,0,&quot;FangSong&quot;,0]</d>'
        items = parse_danmaku(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "求\n你\n了")
        self.assertEqual(items[0]["mode"], "rtl")


class TestDanmakuEndpointCaching(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from miku.app import app

        self.client = app.test_client()

    async def test_danmaku_res_caches_xml_in_redis_and_skips_second_upstream_fetch(self):
        from unittest.mock import AsyncMock, patch

        import miku.media.res as res

        store = {}
        ttls = {}

        async def fake_get(k):
            return store.get(k)

        async def fake_setex(k, ttl, val):
            store[k] = val
            ttls[k] = ttl

        xml_payload = '<i><d p="1.5,1,25,16777215,1600000000">Cached Danmaku</d></i>'
        mock_get_xml = AsyncMock(return_value=xml_payload)

        with (
            patch.object(res.appredis, "get", side_effect=fake_get),
            patch.object(res.appredis, "setex", side_effect=fake_setex),
            patch("miku.api.video.Video.get_danmaku_xml", mock_get_xml),
        ):
            r1 = await self.client.get("/res/danmaku/BV1xx411c7mD:0")
            self.assertEqual(r1.status_code, 200)
            data1 = await r1.get_json()
            self.assertEqual(len(data1), 1)
            self.assertEqual(data1[0]["text"], "Cached Danmaku")
            self.assertEqual(mock_get_xml.await_count, 1)
            self.assertEqual(store.get("miku_danmaku_xml_BV1xx411c7mD_0"), xml_payload)
            self.assertEqual(ttls.get("miku_danmaku_xml_BV1xx411c7mD_0"), res.DANMAKU_CACHE_TTL)

            # Second request must hit Redis cache and NOT call upstream get_danmaku_xml
            r2 = await self.client.get("/res/danmaku/BV1xx411c7mD:0")
            self.assertEqual(r2.status_code, 200)
            data2 = await r2.get_json()
            self.assertEqual(data2, data1)
            self.assertEqual(mock_get_xml.await_count, 1)

    async def test_danmaku_res_does_not_cache_html_block_page(self):
        from unittest.mock import AsyncMock, patch

        import miku.media.res as res

        mock_setex = AsyncMock()
        mock_get_xml = AsyncMock(return_value="<!DOCTYPE html><html><body>412 Precondition Failed</body></html>")

        with (
            patch.object(res.appredis, "get", new=AsyncMock(return_value=None)),
            patch.object(res.appredis, "setex", mock_setex),
            patch("miku.api.video.Video.get_danmaku_xml", mock_get_xml),
        ):
            r = await self.client.get("/res/danmaku/BV1xx411c7mD:0")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(await r.get_json(), [])
            mock_setex.assert_not_awaited()

    async def test_danmaku_res_rejects_invalid_vid_without_touching_upstream(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        import miku.media.res as res

        mock_get = AsyncMock()
        mock_video_cls = MagicMock()

        with (
            patch.object(res.appredis, "get", mock_get),
            patch("miku.api.video.Video", mock_video_cls),
        ):
            r = await self.client.get("/res/danmaku/invalid_vid_123")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(await r.get_json(), [])
            mock_get.assert_not_awaited()
            mock_video_cls.assert_not_called()

            r_neg = await self.client.get("/res/danmaku/BV1xx411c7mD:-1")
            self.assertEqual(r_neg.status_code, 200)
            self.assertEqual(await r_neg.get_json(), [])
            mock_get.assert_not_awaited()
            mock_video_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
