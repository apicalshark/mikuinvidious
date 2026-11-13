import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import miku.core.secrets_encryption as secrets_encryption
import miku.media.refresher as refresher
import rtoml
from miku.api import Credential


class TestRefresher(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="miku_test_refresher_")
        self.cfg_path = Path(self.tmpdir) / "config.toml"
        self.orig_enc_instance = secrets_encryption._encryption_instance
        secrets_encryption._encryption_instance = None

    def tearDown(self):
        secrets_encryption._encryption_instance = self.orig_enc_instance
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_cookies_updates_credential_preserves_fields_escapes_toml_and_chmod_600(self):
        self.cfg_path.write_text(
            "[credential]\n"
            "use_cred = true\n"
            'sessdata = "old_sess"\n'
            'bili_jct = "old_jct"\n'
            'buvid3 = "keep_buvid3"\n'
            'buvid4 = "keep_buvid4"\n'
            'bili_ticket = "stale_ticket"\n'
            'bili_ticket_expires = "12345"\n'
            "\n"
            "[updatedcred]\n"
            'sessdata = "legacy"\n',
            encoding="utf-8",
        )

        # Deliberate TOML-breaking characters in values + bili_ticket present on Credential
        tricky_jct = 'new"jct\\with\nnewline'
        cred = Credential(
            sessdata="new_sess",
            bili_jct=tricky_jct,
            dedeuserid="42",
            ac_time_value="ac_new",
        )
        cred.bili_ticket = "runtime_ticket_should_not_persist"
        cred.bili_ticket_expires = 999999

        refresher.write_cookies(cred, config_path=self.cfg_path)

        parsed = rtoml.load(self.cfg_path)
        self.assertNotIn("updatedcred", parsed)
        cred_sec = parsed["credential"]
        self.assertTrue(cred_sec["use_cred"])
        self.assertEqual(cred_sec["sessdata"], "new_sess")
        self.assertEqual(cred_sec["bili_jct"], tricky_jct)
        self.assertEqual(cred_sec["buvid3"], "keep_buvid3")
        self.assertEqual(cred_sec["buvid4"], "keep_buvid4")
        self.assertEqual(cred_sec["dedeuserid"], "42")
        self.assertEqual(cred_sec["ac_time_value"], "ac_new")
        self.assertNotIn("bili_ticket", cred_sec)
        self.assertNotIn("bili_ticket_expires", cred_sec)

        mode = stat.S_IMODE(os.stat(self.cfg_path).st_mode)
        self.assertEqual(mode, 0o600)

    @unittest.skipUnless(secrets_encryption.NACL_AVAILABLE, "PyNaCl not installed")
    def test_load_and_write_nacl_prefix_and_raw_encryption_roundtrip(self):
        import base64

        import nacl.secret

        key_b64 = base64.b64encode(b"0" * nacl.secret.SecretBox.KEY_SIZE).decode("ascii")
        with patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_b64}):
            secrets_encryption._encryption_instance = None
            enc_sess_prefixed = f"nacl:{secrets_encryption.encrypt_secret('old_sess')}"
            enc_jct_raw = secrets_encryption.encrypt_secret("old_jct")

            self.cfg_path.write_text(
                "[credential]\n"
                "use_cred = true\n"
                f'sessdata = "{enc_sess_prefixed}"\n'
                f'bili_jct = "{enc_jct_raw}"\n'
                'buvid3 = "plain_b3"\n',
                encoding="utf-8",
            )

            loaded = refresher.load_credential(self.cfg_path)
            self.assertEqual(loaded.sessdata, "old_sess")
            self.assertEqual(loaded.bili_jct, "old_jct")
            self.assertEqual(loaded.buvid3, "plain_b3")

            refreshed = Credential(sessdata="refreshed_sess", bili_jct="refreshed_jct")
            refresher.write_cookies(refreshed, config_path=self.cfg_path)

            from miku.services.credential import get_web_api_cookies, init_credentials

            saved_conf = rtoml.load(self.cfg_path)
            auth = init_credentials(saved_conf)
            self.assertEqual(get_web_api_cookies(auth, conf=saved_conf)["SESSDATA"], "refreshed_sess")
            self.assertEqual(auth.bili_jct, "refreshed_jct")
            saved = saved_conf["credential"]
            self.assertTrue(saved["sessdata"].startswith("nacl:"))
            self.assertEqual(secrets_encryption.decrypt_secret(saved["sessdata"][5:]), "refreshed_sess")
            self.assertFalse(saved["bili_jct"].startswith("nacl:"))
            self.assertEqual(secrets_encryption.decrypt_secret(saved["bili_jct"]), "refreshed_jct")
            self.assertEqual(saved["buvid3"], "plain_b3")


class TestUnknownConfirmationPersistence(unittest.TestCase):
    def test_renew_saves_candidate_and_reports_unconfirmed(self):
        from unittest.mock import AsyncMock

        from miku.api.credential import RefreshConfirmationUnknown

        candidate = Credential(sessdata="new_sess", ac_time_value="new_token")
        cred = Credential(sessdata="old_sess")
        cred.check_refresh = AsyncMock(return_value=True)
        cred.refresh = AsyncMock(side_effect=RefreshConfirmationUnknown(candidate))
        with patch.object(refresher, "write_cookies") as save:
            self.assertFalse(refresher.renew_cookies(cred, config_path="test.toml"))
        save.assert_called_once_with(candidate, config_path="test.toml")
        self.assertEqual(cred.sessdata, "old_sess")


if __name__ == "__main__":
    unittest.main()
