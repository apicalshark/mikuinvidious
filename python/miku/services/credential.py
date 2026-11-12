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
