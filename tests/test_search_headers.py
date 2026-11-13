import unittest

from miku.api.search import _headers_for


class SearchHeaders(unittest.TestCase):
    def test_keyword_search_uses_search_page_headers(self):
        for param in ("keyword", "term"):
            with self.subTest(param=param):
                headers = _headers_for("https://api.bilibili.com/x/web-interface/wbi/search/type", {param: "a b&c"})
                self.assertEqual(headers["origin"], "https://search.bilibili.com")
                self.assertEqual(headers["referer"], "https://search.bilibili.com/all?keyword=a%20b%26c")

    def test_other_requests_use_main_page_headers(self):
        for url, params in (
            ("https://api.bilibili.com/x/web-interface/nav", {}),
            ("https://example.com/", {"keyword": "test"}),
        ):
            with self.subTest(url=url):
                headers = _headers_for(url, params)
                self.assertEqual(headers["origin"], "https://www.bilibili.com")
                self.assertEqual(headers["referer"], "https://www.bilibili.com/")
