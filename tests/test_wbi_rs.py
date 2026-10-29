import hashlib
import time
import unittest
import urllib.parse

from api.client import _enc_wbi, _mixin_from_keys

try:
    from miku_core_rs import mixin_from_keys, sign_wbi_params
except ImportError:
    sign_wbi_params = None
    mixin_from_keys = None


class TestWbiSigner(unittest.TestCase):
    def setUp(self):
        self.img_key = "7cd084481368485c8e428b3d1f3db9ed"
        self.sub_key = "411145e16d424f39af363d595240d7e3"
        self.mixin_key = _mixin_from_keys(self.img_key, self.sub_key)

    def test_rust_wbi_import(self):
        self.assertIsNotNone(sign_wbi_params, "sign_wbi_params should be exported by miku_core_rs")
        self.assertIsNotNone(mixin_from_keys, "mixin_from_keys should be exported by miku_core_rs")

    def test_mixin_from_keys(self):
        expected = _mixin_from_keys(self.img_key, self.sub_key)
        actual = mixin_from_keys(self.img_key, self.sub_key)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 32)

    def test_sign_wbi_params_matches_python(self):
        fixed_wts = 1700000000
        params = {
            "bvid": "BV1xx411c7xx",
            "cid": 12345678,
            "keyword": "初音ミク live 2026",
            "page": 1,
            "filter": "all&clean",
        }
        # Run python version with fixed wts
        py_params = dict(params)
        py_params["wts"] = fixed_wts
        if py_params.get("web_location") is None:
            py_params["web_location"] = 1315873
        query = "&".join(
            f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(py_params.items())
        )
        py_w_rid = hashlib.md5((query + self.mixin_key).encode("utf-8")).hexdigest()
        py_params["w_rid"] = py_w_rid

        # Run rust version
        rs_params = sign_wbi_params(dict(params), self.mixin_key, wts=fixed_wts)

        self.assertEqual(rs_params["wts"], py_params["wts"])
        self.assertEqual(rs_params["web_location"], py_params["web_location"])
        self.assertEqual(rs_params["w_rid"], py_params["w_rid"])
        self.assertEqual(rs_params, py_params)

    def test_sign_wbi_client_enc_wbi_integration(self):
        params = {"bvid": "BV1xx411c7xx", "order": "pubdate"}
        signed = _enc_wbi(params, self.mixin_key)
        self.assertIn("w_rid", signed)
        self.assertIn("wts", signed)
        self.assertEqual(signed["web_location"], 1315873)


if __name__ == "__main__":
    unittest.main()
