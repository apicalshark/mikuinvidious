import subprocess
import sys
import unittest

from miku.api.exceptions import ArgsException
from miku.api.video import av2bv, bv2av


class TestBvid(unittest.TestCase):
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
        self.assertEqual(av2bv("170001"), "BV17x411w7KC")
        self.assertEqual(av2bv("av170001"), "BV17x411w7KC")
        self.assertEqual(bv2av(av2bv(170001)), 170001)

    def test_av2bv_reversible_boundaries(self):
        for aid in (1, (1 << 51) - 1):
            with self.subTest(aid=aid):
                self.assertEqual(bv2av(av2bv(aid)), aid)

    def test_fresh_process_import(self):
        result = subprocess.run(
            [sys.executable, "-c", (
                "import miku.api.bvid as b; "
                "assert b.av2bv(170001) == 'BV17x411w7KC'; "
                "assert b.bv2av('BV17x411w7KC') == 170001"
            )],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_bvid_module_import(self):
        import miku.api.bvid as bvid

        self.assertEqual(bvid.av2bv(170001), "BV17x411w7KC")
        self.assertEqual(bvid.bv2av("BV17x411w7KC"), 170001)

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
        invalid_aids = [0, -1, -99999, 1 << 51, (1 << 51) + 1, 1 << 100, str(1 << 51), "av" + str(1 << 51), 1.5, None]
        for inv in invalid_aids:
            with self.subTest(inv=inv):
                with self.assertRaises(ArgsException):
                    av2bv(inv)


if __name__ == "__main__":
    unittest.main()
