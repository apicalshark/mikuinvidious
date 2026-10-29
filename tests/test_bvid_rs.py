import unittest

from api.exceptions import ArgsException
import api.video as video_mod
from api.video import av2bv, bv2av

try:
    import miku_core_rs
except ModuleNotFoundError as exc:
    if exc.name != "miku_core_rs":
        raise
    miku_core_rs = None


class TestBvidRs(unittest.TestCase):
    def test_bv2av_parity(self):
        cases = [
            ("BV17x411w7KC", 170001),
            ("BV1Q541167Qg", 455017605),
            ("BV1xx411c7mD", 2),
        ]
        for bvid, expected_aid in cases:
            with self.subTest(bvid=bvid):
                aid = bv2av(bvid)
                self.assertEqual(aid, expected_aid)
                # Round-trip check
                re_bvid = av2bv(expected_aid)
                self.assertEqual(re_bvid, bvid)
                self.assertEqual(bv2av(re_bvid), expected_aid)

    def test_av2bv_valid(self):
        self.assertEqual(av2bv(170001), "BV17x411w7KC")
        self.assertEqual(bv2av(av2bv(170001)), 170001)

    def test_invalid_bvid_raises(self):
        invalid_bvids = [
            "",
            "not_a_bvid",
            "av12345",
            "BV123",  # too short
            "BV1xx411c7xxExtra",  # too long
            "BV1xx411c7x?",  # invalid char
            12345,  # non-string
        ]
        for inv in invalid_bvids:
            with self.subTest(inv=inv):
                with self.assertRaises(ArgsException):
                    bv2av(inv)

    def test_invalid_aid_raises(self):
        invalid_aids = [0, -1, -99999]
        for inv in invalid_aids:
            with self.subTest(inv=inv):
                with self.assertRaises(ArgsException):
                    av2bv(inv)

    @unittest.skipIf(miku_core_rs is None, "miku_core_rs is not installed")
    def test_rust_direct_exports(self):
        self.assertTrue(hasattr(miku_core_rs, "bv2av"))
        self.assertTrue(hasattr(miku_core_rs, "av2bv"))
        self.assertEqual(miku_core_rs.bv2av("BV17x411w7KC"), 170001)
        self.assertEqual(miku_core_rs.av2bv(170001), "BV17x411w7KC")


if __name__ == "__main__":
    unittest.main()
