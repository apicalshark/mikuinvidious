import hashlib
import unittest
import urllib.parse

from miku.api.client import _enc_wbi, _mixin_from_keys


class TestWbiSigner(unittest.TestCase):
    def setUp(self):
        self.img_key = "7cd084481368485c8e428b3d1f3db9ed"
        self.sub_key = "411145e16d424f39af363d595240d7e3"
        self.mixin_key = _mixin_from_keys(self.img_key, self.sub_key)

    def test_mixin_from_keys(self):
        actual = _mixin_from_keys(self.img_key, self.sub_key)
        self.assertEqual(len(actual), 32)

    def test_sign_wbi_params(self):
        fixed_wts = 1700000000
        params = {
            "bvid": "BV1xx411c7xx",
            "cid": 12345678,
            "keyword": "初音ミク live 2026",
            "page": 1,
            "filter": "all&clean",
        }
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

        signed = _enc_wbi(dict(params), self.mixin_key)
        self.assertIn("w_rid", signed)
        self.assertIn("wts", signed)
        self.assertEqual(signed["web_location"], 1315873)

    def test_sign_wbi_client_enc_wbi_integration(self):
        params = {"bvid": "BV1xx411c7xx", "order": "pubdate"}
        signed = _enc_wbi(params, self.mixin_key)
        self.assertIn("w_rid", signed)
        self.assertIn("wts", signed)
        self.assertEqual(signed["web_location"], 1315873)

    def test_miku_api_wbi_direct(self):
        from miku.api.wbi import enc_wbi, mixin_from_keys

        mixin = mixin_from_keys(self.img_key, self.sub_key)
        self.assertEqual(mixin, self.mixin_key)
        params = {"bvid": "BV1xx411c7xx"}
        signed = enc_wbi(params, mixin)
        self.assertIn("w_rid", signed)
        self.assertIn("wts", signed)

    def test_oe_permutation_table_valid(self):
        from miku.api.client import OE as client_oe
        from miku.api.wbi import OE as wbi_oe

        self.assertIs(client_oe, wbi_oe)
        self.assertEqual(len(wbi_oe), 64)
        self.assertEqual(set(wbi_oe), set(range(64)))


if __name__ == "__main__":
    unittest.main()
