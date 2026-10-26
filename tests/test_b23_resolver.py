import unittest
from unittest.mock import AsyncMock, MagicMock
import httpx

from b23_resolver import (
    extract_b23_or_bili_url,
    clean_bilibili_url,
    resolve_b23_target,
)


class TestExtractUrl:
    def test_extract_plain_b23_url(self):
        assert extract_b23_or_bili_url("https://b23.tv/abc1234") == "https://b23.tv/abc1234"
        assert extract_b23_or_bili_url("http://b23.tv/XYZ789") == "https://b23.tv/XYZ789"
        assert extract_b23_or_bili_url("b23.tv/abc1234") == "https://b23.tv/abc1234"

    def test_extract_from_app_share_text(self):
        text = "【2026最新混剪】 https://b23.tv/xyz789 赶紧来看！"
        assert extract_b23_or_bili_url(text) == "https://b23.tv/xyz789"

        text_with_newlines = "🔥 精彩推荐\nhttps://b23.tv/abc1234?share_medium=android\n点击链接查看"
        assert extract_b23_or_bili_url(text_with_newlines) == "https://b23.tv/abc1234"

    def test_extract_full_bilibili_url(self):
        url = "https://www.bilibili.com/video/BV1xx411c7xx?spm_id_from=333.788"
        assert extract_b23_or_bili_url(url) == url

        text = "快看：https://m.bilibili.com/video/BV1xx411c7xx 很好看"
        assert extract_b23_or_bili_url(text) == "https://m.bilibili.com/video/BV1xx411c7xx"

    def test_ssrf_and_invalid_domains_rejected(self):
        assert extract_b23_or_bili_url("https://attacker.com/b23.tv/xyz123") is None
        assert extract_b23_or_bili_url("https://notb23.tv/xyz123") is None
        assert extract_b23_or_bili_url("https://b23.tv.evil.com/xyz123") is None
        assert extract_b23_or_bili_url("https://bilibili.com.attacker.com/video/BV1xx") is None
        assert extract_b23_or_bili_url("random text without url") is None
        assert extract_b23_or_bili_url("") is None
        assert extract_b23_or_bili_url(None) is None


class TestCleanBilibiliUrl:
    def test_clean_tracking_parameters(self):
        dirty = (
            "https://www.bilibili.com/video/BV1xx411c7xx"
            "?spm_id_from=333.788.0.0"
            "&buvid=XY123"
            "&share_medium=android"
            "&share_plat=android"
            "&share_source=COPY"
            "&share_tag=s_share"
            "&unique_k=abc"
            "&ts=1700000000"
            "&from_spmid=123"
            "&from_source=copy"
        )
        res = clean_bilibili_url(dirty)
        assert res["clean_url"] == "https://www.bilibili.com/video/BV1xx411c7xx"
        assert res["type"] == "video"
        assert res["id"] == "BV1xx411c7xx"
        assert res["local_path"] == "/video/BV1xx411c7xx"

    def test_preserve_functional_params(self):
        url = "https://www.bilibili.com/video/BV1xx411c7xx?p=2&t=60&spm_id_from=333.788"
        res = clean_bilibili_url(url)
        assert "p=2" in res["clean_url"]
        assert "t=60" in res["clean_url"]
        assert "spm_id_from" not in res["clean_url"]
        assert res["local_path"] == "/video/BV1xx411c7xx:2"

    def test_classify_video_av(self):
        url = "https://www.bilibili.com/video/av170001?spm_id_from=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "video"
        assert res["id"] == "av170001"
        assert res["clean_url"] == "https://www.bilibili.com/video/av170001"
        assert res["local_path"] == "/video/av170001"

    def test_classify_bangumi_ep(self):
        url = "https://www.bilibili.com/bangumi/play/ep12345?spm_id_from=333.788"
        res = clean_bilibili_url(url)
        assert res["type"] == "bangumi_ep"
        assert res["id"] == "ep12345"
        assert res["clean_url"] == "https://www.bilibili.com/bangumi/play/ep12345"
        assert res["local_path"] == "/bangumi/play/ep12345"

    def test_classify_bangumi_ss(self):
        url = "https://www.bilibili.com/bangumi/play/ss12345?from_spmid=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "bangumi_ss"
        assert res["id"] == "ss12345"
        assert res["clean_url"] == "https://www.bilibili.com/bangumi/play/ss12345"
        assert res["local_path"] in ("/bangumi/play/ss12345", "/bangumi/view/12345")

    def test_classify_read_cv(self):
        url = "https://www.bilibili.com/read/cv12345?spm_id_from=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "read"
        assert res["id"] == "cv12345"
        assert res["clean_url"] == "https://www.bilibili.com/read/cv12345"
        assert res["local_path"] == "/read/cv12345"

    def test_classify_read_mobile(self):
        url = "https://www.bilibili.com/read/mobile/12345?spm_id_from=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "read"
        assert res["id"] == "cv12345"
        assert res["local_path"] == "/read/cv12345"

    def test_classify_opus(self):
        url = "https://www.bilibili.com/opus/123456789?spm_id_from=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "opus"
        assert res["id"] == "opus123456789"
        assert res["clean_url"] == "https://www.bilibili.com/opus/123456789"
        assert res["local_path"] == "/read/opus123456789"

    def test_classify_live(self):
        url = "https://live.bilibili.com/12345?broadcast_type=0&is_story_h5=0"
        res = clean_bilibili_url(url)
        assert res["type"] == "live"
        assert res["id"] == "12345"
        assert res["clean_url"] == "https://live.bilibili.com/12345"
        assert res["local_path"] == "/live/12345"

    def test_classify_space(self):
        url = "https://space.bilibili.com/123456?spm_id_from=333"
        res = clean_bilibili_url(url)
        assert res["type"] == "space"
        assert res["id"] == "123456"
        assert res["clean_url"] == "https://space.bilibili.com/123456"
        assert res["local_path"] == "/space/123456"

    def test_classify_audio(self):
        url_au = "https://www.bilibili.com/audio/au12345?spm_id_from=333"
        res_au = clean_bilibili_url(url_au)
        assert res_au["type"] == "audio"
        assert res_au["id"] == "au12345"
        assert res_au["clean_url"] == "https://www.bilibili.com/audio/au12345"
        assert res_au["local_path"] == "/audio/au12345"

        url_am = "https://www.bilibili.com/audio/am67890?spm_id_from=333"
        res_am = clean_bilibili_url(url_am)
        assert res_am["type"] == "audio_list"
        assert res_am["id"] == "am67890"
        assert res_am["clean_url"] == "https://www.bilibili.com/audio/am67890"
        assert res_am["local_path"] in ("/audio_list/am67890", "/audio/am67890")


class TestResolveB23Target(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_direct_bilibili_url(self):
        input_text = "https://www.bilibili.com/video/BV1xx411c7xx?spm_id_from=333"
        res = await resolve_b23_target(input_text)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["type"], "video")
        self.assertEqual(res["clean_url"], "https://www.bilibili.com/video/BV1xx411c7xx")
        self.assertEqual(res["local_path"], "/video/BV1xx411c7xx")
        self.assertIsNone(res["error"])

    async def test_resolve_b23_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {
            "Location": "https://www.bilibili.com/video/BV1xx411c7xx?spm_id_from=333.788&buvid=123"
        }
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        res = await resolve_b23_target("【分享】https://b23.tv/abc1234 看看", client=mock_client)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["target_url"].startswith("https://www.bilibili.com/video/BV1xx411c7xx"))
        self.assertEqual(res["clean_url"], "https://www.bilibili.com/video/BV1xx411c7xx")
        self.assertEqual(res["local_path"], "/video/BV1xx411c7xx")
        self.assertEqual(res["type"], "video")
        self.assertEqual(res["id"], "BV1xx411c7xx")
        self.assertIsNone(res["error"])
        mock_client.get.assert_called_once()

    async def test_resolve_b23_redirect_to_bangumi(self):
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {
            "Location": "https://www.bilibili.com/bangumi/play/ep12345?from_spmid=333"
        }
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        res = await resolve_b23_target("https://b23.tv/ep123", client=mock_client)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["clean_url"], "https://www.bilibili.com/bangumi/play/ep12345")
        self.assertEqual(res["local_path"], "/bangumi/play/ep12345")
        self.assertEqual(res["type"], "bangumi_ep")
        self.assertEqual(res["id"], "ep12345")

    async def test_resolve_ssrf_destination_rejected(self):
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        res = await resolve_b23_target("https://b23.tv/evil12", client=mock_client)
        self.assertEqual(res["status"], "error")
        self.assertIn("Invalid or unsafe redirect destination", res["error"])

    async def test_resolve_non_redirect_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        res = await resolve_b23_target("https://b23.tv/notfound", client=mock_client)
        self.assertEqual(res["status"], "error")
        self.assertIn("404", res["error"])

    async def test_resolve_no_url_found(self):
        res = await resolve_b23_target("just plain text without link")
        self.assertEqual(res["status"], "error")
        self.assertIn("No valid", res["error"])
