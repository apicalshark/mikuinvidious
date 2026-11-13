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
"""Credential decryption and lifecycle management."""

from __future__ import annotations

import base64
from typing import Any

import nacl.secret

from miku.api.credential import Credential
from miku.core.secrets_encryption import NACL_AVAILABLE, decrypt_secret


def decrypt_if_encrypted(value: str | None) -> str:
    """Decrypt value if it appears to be encrypted with NaCl SecretBox."""
    if not value:
        return "" if value is None else value
    if value.startswith("nacl:"):
        return decrypt_secret(value[5:])
    try:
        if NACL_AVAILABLE:
            data = base64.b64decode(value)
            if len(data) > nacl.secret.SecretBox.NONCE_SIZE:
                try:
                    return decrypt_secret(value)
                except Exception as e:
                    print(f"[Security] Failed to decrypt credential: {e}. Using raw value.")
    except Exception:
        pass
    return value


_TICKET_COOKIE_NAMES = frozenset({"bili_ticket", "bili_ticket_expires"})


def is_cred_active(cred: Credential | None, conf: dict[str, Any] | None = None) -> bool:
    """Return True when credential auth is enabled in config and *cred* is present."""
    if cred is None:
        return False
    if conf is None:
        import miku.state as shared

        conf = shared.appconf
    use_cred = (conf.get("credential") or {}).get("use_cred", False)
    if isinstance(use_cred, str):
        return use_cred.strip().lower() == "true"
    return bool(use_cred)


def get_web_api_cookies(cred: Credential | None, conf: dict[str, Any] | None = None) -> dict[str, str]:
    """Return non-empty web API cookies for *cred*, never including bili_ticket."""
    if not is_cred_active(cred, conf=conf):
        return {}
    assert cred is not None
    return {k: v for k, v in cred.get_cookies().items() if v and k not in _TICKET_COOKIE_NAMES}


def apply_cdn_cred_headers(
    headers: dict[str, str],
    cred: Credential | None,
    ticket: str | None = None,
    conf: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Apply x-bili-ticket header and decrypted credential headers/cookies for CDN requests."""
    if ticket:
        headers["x-bili-ticket"] = ticket
    else:
        headers.pop("x-bili-ticket", None)

    if not is_cred_active(cred, conf=conf):
        return headers

    assert cred is not None
    buvid3 = getattr(cred, "buvid3", None)
    buvid4 = getattr(cred, "buvid4", None)
    if buvid3:
        headers["buvid"] = buvid3
    if buvid4:
        headers["buvid4"] = buvid4

    cookie_pairs = [
        (name, value)
        for name, value in (
            ("SESSDATA", getattr(cred, "sessdata", None)),
            ("bili_jct", getattr(cred, "bili_jct", None)),
            ("buvid3", buvid3),
            ("buvid4", buvid4),
            ("DedeUserID", getattr(cred, "dedeuserid", None)),
        )
        if value
    ]
    headers.pop("cookie", None)
    if cookie_pairs:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_pairs)
    else:
        headers.pop("Cookie", None)
    return headers


def init_credentials(conf: dict[str, Any]) -> Credential | None:
    """Initialize Bilibili API Credential from configuration."""
    cred_conf = conf.get("credential", {})

    if not cred_conf.get("use_cred") and cred_conf.get("sessdata"):
        print("[Init] SESSDATA provided; enabling credential support (use_cred=True).")
        cred_conf["use_cred"] = True

    if not cred_conf.get("use_cred"):
        return None

    return Credential(
        sessdata=decrypt_if_encrypted(cred_conf.get("sessdata")),
        bili_jct=decrypt_if_encrypted(cred_conf.get("bili_jct")),
        buvid3=decrypt_if_encrypted(cred_conf.get("buvid3")),
        buvid4=decrypt_if_encrypted(cred_conf.get("buvid4", "")),
        dedeuserid=decrypt_if_encrypted(cred_conf.get("dedeuserid")),
        ac_time_value=decrypt_if_encrypted(cred_conf.get("ac_time_value")),
    )
