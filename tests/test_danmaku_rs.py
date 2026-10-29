import unittest
from unittest.mock import patch
from xml.dom import minidom

import danmaku
from danmaku import danmaku_xml_conv, danmaku_xml_fallback, parse_danmaku


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

    @unittest.skipIf(danmaku.parse_danmaku_xml is None, "miku_core_rs is not installed")
    def test_native_errors_trigger_fallback_and_preserve_entries(self):
        valid = '<d p="1,1,25,16777215">Valid &amp; intact</d>'
        for broken in ("<broken></mismatch>", '<d p="2,1,25,16777215">Unknown &entity;</d>'):
            with self.subTest(broken=broken):
                xml = f"<i>{valid}{broken}{valid}</i>"
                with self.assertRaises(ValueError):
                    danmaku.parse_danmaku_xml(xml)
                with patch("danmaku.danmaku_xml_fallback", wraps=danmaku_xml_fallback) as fallback:
                    items = parse_danmaku(xml)
                fallback.assert_called_once_with(xml)
                self.assertEqual(items, danmaku_xml_fallback(xml))
                self.assertEqual(items[0]["text"], "Valid & intact")
                self.assertEqual(items[-1]["text"], "Valid & intact")

    @unittest.skipIf(danmaku.parse_danmaku_xml is None, "miku_core_rs is not installed")
    def test_native_normal_eof_salvages_open_entry(self):
        items = danmaku.parse_danmaku_xml('<i><d p="1,1,25,16777215">Unclosed text')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Unclosed text")


if __name__ == "__main__":
    unittest.main()
