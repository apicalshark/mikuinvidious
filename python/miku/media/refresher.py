# Copyright (C) 2023 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import base64
import os
import tempfile
from pathlib import Path
from typing import Any

import rtoml

from miku.api import Credential, sync
from miku.api.credential import RefreshConfirmationUnknown
from miku.core.secrets_encryption import NACL_AVAILABLE, decrypt_secret, encrypt_secret

_CRED_FIELDS = ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value")
_PRESERVE_IF_EMPTY = frozenset({"buvid3", "buvid4"})


def _inspect_cred_value(raw: Any) -> tuple[str | None, str]:
    """Return ``(encryption_mode, plaintext)`` for a stored credential value.

    ``encryption_mode`` is ``"nacl_prefix"`` for ``nacl:...`` values,
    ``"raw"`` for bare base64 NaCl SecretBox ciphertexts, or ``None`` for plaintext.
    """
    if not isinstance(raw, str) or not raw:
        return None, "" if raw is None else str(raw)
    if raw.startswith("nacl:"):
        return "nacl_prefix", decrypt_secret(raw[5:])
    if NACL_AVAILABLE:
        try:
            import nacl.secret

            data = base64.b64decode(raw, validate=True)
            if len(data) > nacl.secret.SecretBox.NONCE_SIZE:
                return "raw", decrypt_secret(raw)
        except Exception:
            pass
    return None, raw


def _encrypt_with_mode(value: str, mode: str | None) -> str:
    if not value or mode is None:
        return value
    ciphertext = encrypt_secret(value)
    return f"nacl:{ciphertext}" if mode == "nacl_prefix" else ciphertext


def load_credential(config_path: str | Path = "config.toml") -> Credential:
    """Load and decrypt `[credential]` from *config_path*."""
    conf = rtoml.load(Path(config_path))
    credstore = conf.get("credential") or {}
    decrypted = {field: _inspect_cred_value(credstore.get(field))[1] for field in _CRED_FIELDS}
    return Credential(
        sessdata=decrypted["sessdata"] or None,
        bili_jct=decrypted["bili_jct"] or None,
        buvid3=decrypted["buvid3"] or None,
        buvid4=decrypted["buvid4"] or None,
        dedeuserid=decrypted["dedeuserid"] or None,
        ac_time_value=decrypted["ac_time_value"] or None,
    )


def renew_cookies(cred: Credential, config_path: str | Path = "config.toml") -> bool:
    try:
        if sync(cred.check_refresh()):
            try:
                sync(cred.refresh())
            except RefreshConfirmationUnknown as e:
                write_cookies(e.credential, config_path=config_path)
                print("Cookie refresh confirmation unknown; new credentials saved.")
                return False
            write_cookies(cred, config_path=config_path)
            print("Cookies refreshed.")
            return True
    except Exception as e:
        print(f"Error refreshing cookies: {e}")
    return False


def write_cookies(cred: Credential, config_path: str | Path = "config.toml") -> None:
    """Update `[credential]` in *config_path* atomically with safe TOML escaping."""
    cfg_path = Path(config_path)
    conf = rtoml.load(cfg_path) if cfg_path.exists() else {}
    conf.pop("updatedcred", None)

    existing_cred = dict(conf.get("credential") or {})
    enc_modes: dict[str, str] = {}
    for key, raw_val in list(existing_cred.items()):
        if key.lower().startswith("bili_ticket"):
            existing_cred.pop(key, None)
            continue
        if key in _CRED_FIELDS and isinstance(raw_val, str) and raw_val:
            mode, _ = _inspect_cred_value(raw_val)
            if mode is not None:
                enc_modes[key] = mode

    raw_cookies = cred.get_cookies() if hasattr(cred, "get_cookies") else {}
    cookie_map = {k.lower(): v for k, v in raw_cookies.items() if not k.lower().startswith("bili_ticket")}
    for field in _CRED_FIELDS:
        val = cookie_map.get(field)
        if not val:
            val = getattr(cred, field, None) or ""
        if not val and field in _PRESERVE_IF_EMPTY and existing_cred.get(field):
            continue
        if val:
            existing_cred[field] = _encrypt_with_mode(str(val), enc_modes.get(field))

    for key, val in cookie_map.items():
        if key in _CRED_FIELDS or key == "use_cred" or not val:
            continue
        existing_cred[key] = str(val)

    conf["credential"] = existing_cred
    toml_text = rtoml.dumps(conf)

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{cfg_path.name}.", suffix=".tmp", dir=str(cfg_path.parent))
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(toml_text)
        os.replace(tmp_name, cfg_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    print("Trying to refresh the cookies...")
    appcred = load_credential("config.toml")

    if renew_cookies(appcred, config_path="config.toml"):
        print("Successfully refreshed the cookies.")
    else:
        print("Cookies are already up-to-date.")
