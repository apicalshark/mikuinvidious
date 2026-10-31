import unittest

from fingerprints import (
    CDN_CHROME_UA,
    CURL_IMPERSONATE,
    CURL_UA,
    DEFAULT_APP_UA,
    FIXED_CHROME_UA,
    get_app_headers,
    get_article_scrape_headers,
    get_browser_headers,
    get_cdn_headers,
    get_web_api_headers,
)


class TestFingerprints(unittest.TestCase):
    def test_default_app_headers(self):
        headers = get_app_headers()
        self.assertEqual(headers["User-Agent"], DEFAULT_APP_UA)
        self.assertIn("BiliDroid", headers["User-Agent"])
        self.assertEqual(headers["Referer"], "https://www.bilibili.com")
        self.assertEqual(headers["env"], "prod")
        self.assertEqual(headers["app-key"], "android64")
        self.assertEqual(headers["x-bili-metadata-ip-region"], "CN")
        self.assertEqual(headers["x-bili-metadata-legal-region"], "CN")

    def test_custom_app_headers(self):
        custom_conf = {
            "user_agent": "CustomBiliDroid/1.0",
            "referer": "https://live.bilibili.com",
            "env": "test",
            "app_key": "custom_key",
            "ip_region": "HK",
            "legal_region": "HK",
        }
        headers = get_app_headers(custom_conf)
        self.assertEqual(headers["User-Agent"], "CustomBiliDroid/1.0")
        self.assertEqual(headers["Referer"], "https://live.bilibili.com")
        self.assertEqual(headers["env"], "test")
        self.assertEqual(headers["app-key"], "custom_key")
        self.assertEqual(headers["x-bili-metadata-ip-region"], "HK")
        self.assertEqual(headers["x-bili-metadata-legal-region"], "HK")

    def test_browser_headers_order_and_hints(self):
        origin = "https://space.bilibili.com"
        referer = "https://space.bilibili.com/12345"
        headers = get_browser_headers(origin=origin, referer=referer)

        # Insertion order must be strictly preserved for HTTP/2 fingerprinting
        keys = list(headers.keys())
        expected_keys = [
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "user-agent",
            "accept",
            "origin",
            "sec-fetch-site",
            "sec-fetch-mode",
            "sec-fetch-dest",
            "referer",
            "accept-encoding",
            "accept-language",
            "priority",
        ]
        self.assertEqual(keys, expected_keys)
        self.assertEqual(headers["user-agent"], CURL_UA)
        self.assertEqual(CURL_IMPERSONATE, "chrome152")
        self.assertIn('"Google Chrome";v="152"', headers["sec-ch-ua"])
        self.assertEqual(headers["sec-ch-ua-mobile"], "?0")
        self.assertEqual(headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(headers["origin"], origin)
        self.assertEqual(headers["referer"], referer)

    def test_unified_chrome_version(self):
        # All Chrome profiles must be unified to Chrome 152
        self.assertEqual(CURL_IMPERSONATE, "chrome152")
        self.assertEqual(CURL_UA, FIXED_CHROME_UA)
        self.assertEqual(CURL_UA, CDN_CHROME_UA)
        self.assertIn("Chrome/152.0.0.0", CURL_UA)

    def test_cdn_headers(self):
        headers = get_cdn_headers()
        self.assertEqual(headers["User-Agent"], CDN_CHROME_UA)
        self.assertEqual(headers["Referer"], "https://www.bilibili.com")
        self.assertEqual(headers["Origin"], "https://www.bilibili.com")
        self.assertEqual(headers["Accept"], "*/*")
        # Ensure BiliDroid is NOT in CDN headers
        self.assertNotIn("BiliDroid", headers["User-Agent"])

    def test_web_api_headers(self):
        headers = get_web_api_headers()
        self.assertEqual(headers["User-Agent"], FIXED_CHROME_UA)
        self.assertEqual(headers["Referer"], "https://www.bilibili.com")
        self.assertEqual(headers["Accept-Language"], "zh-CN,zh;q=0.9")

    def test_article_scrape_headers(self):
        headers = get_article_scrape_headers()
        self.assertEqual(headers["User-Agent"], DEFAULT_APP_UA)
        self.assertIn("BiliDroid/8.83.0", headers["User-Agent"])

        custom = get_article_scrape_headers({"user_agent": "CustomBiliDroid/2.0"})
        self.assertEqual(custom["User-Agent"], "CustomBiliDroid/2.0")

    def test_unified_app_ua(self):
        # All App profiles must be unified to DEFAULT_APP_UA (BiliDroid/8.83.0)
        from fingerprints import APP_UA, ARTICLE_SCRAPE_UA

        self.assertEqual(APP_UA, DEFAULT_APP_UA)
        self.assertEqual(ARTICLE_SCRAPE_UA, DEFAULT_APP_UA)
        self.assertIn("8.83.0", APP_UA)


if __name__ == "__main__":
    unittest.main()
