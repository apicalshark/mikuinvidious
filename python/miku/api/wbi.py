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
"""Bilibili WBI request signing and key management."""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any

# OE permutation table used to derive the wbi mixin key
OE = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]


# Characters stripped from parameter values during WBI signing (Bilibili spec).
_WBI_FILTER_TABLE = str.maketrans("", "", "!'()*")

# Short TTL (seconds) when falling back to static keys after /nav failure.
_WBI_FALLBACK_TTL = 300.0


def mixin_from_keys(img_key: str, sub_key: str) -> str:
    """Derive the 32-character WBI mixin key from raw img and sub keys."""
    ae = img_key + sub_key
    return "".join(ae[i] for i in OE if i < len(ae))[:32]


def encode_wbi_query(params: dict[str, Any]) -> str:
    """Serialize query parameters using sorted %20 percent-encoding matching enc_wbi."""
    return "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items())
    )


def enc_wbi(params: dict[str, Any], mixin_key: str) -> dict[str, Any]:
    """Calculate and append wts and w_rid signatures to query parameters."""
    if not mixin_key:
        raise ValueError("Empty WBI mixin key")

    params.pop("w_rid", None)
    params["wts"] = int(time.time())
    if params.get("web_location") is None:
        params["web_location"] = 1315873

    for k, v in list(params.items()):
        if isinstance(v, str):
            params[k] = v.translate(_WBI_FILTER_TABLE)

    # Match PipePipe / bilibili-API-collect: sort keys, then percent-encode each
    # pair with %20 (NOT '+' which breaks WBI signatures on space characters).
    query = "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v).translate(_WBI_FILTER_TABLE), safe='')}"
        for k, v in sorted(params.items())
    )
    params["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return params
