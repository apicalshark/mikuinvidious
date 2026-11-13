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
"""Canonical Bilibili AV/BV identifier conversion utilities.

Matches PipePipe's DeviceForger utils (the modern algorithm Bilibili uses
for current bvid formats).
"""

from __future__ import annotations

import re

_XOR_CODE = 23442827791579
_MASK_CODE = 2251799813685247
_MAX_AID = 1 << 51
_BASE = 58
_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
_ITABLE = {c: i for i, c in enumerate(_TABLE)}


def bv2av(x: str) -> int:
    """Convert a 12-character Bilibili BV identifier to its numeric AV (aid).

    Raises ArgsException if x is not a valid 12-character BV ID.
    """
    if (
        not isinstance(x, str)
        or not re.fullmatch(r"BV[a-zA-Z0-9]{10}", x)
        or any(char not in _ITABLE for char in x[3:])
    ):
        from miku.api.exceptions import ArgsException

        raise ArgsException(
            "Invalid bvid: must be a 12-character case-sensitive string starting with BV, containing only letters and digits."
        )
    arr = list(x)
    arr[3], arr[9] = arr[9], arr[3]
    arr[4], arr[7] = arr[7], arr[4]
    sub = "".join(arr[3:])
    tmp = 0
    for c in sub:
        tmp = tmp * _BASE + _ITABLE[c]
    return (tmp & _MASK_CODE) ^ _XOR_CODE


def av2bv(x: int | str) -> str:
    """Convert a numeric AV ID (or string like '170001' or 'av170001') to a 12-character BV string.

    Raises ArgsException unless the parsed aid is an integer with 0 < aid < 2**51.
    """
    if isinstance(x, str):
        if x.startswith("av") or x.startswith("AV"):
            x = x[2:]
        try:
            x = int(x)
        except ValueError:
            from miku.api.exceptions import ArgsException

            raise ArgsException("Invalid aid: must be an integer or numeric string.") from None

    if not isinstance(x, int) or not 0 < x < _MAX_AID:
        from miku.api.exceptions import ArgsException

        raise ArgsException(f"aid must be greater than 0 and less than {_MAX_AID}.")

    tmp = (_MAX_AID | x) ^ _XOR_CODE
    r = list("BV1000000000")
    idx = len(r) - 1
    while tmp > 0:
        r[idx] = _TABLE[tmp % _BASE]
        tmp //= _BASE
        idx -= 1
    r[3], r[9] = r[9], r[3]
    r[4], r[7] = r[7], r[4]
    return "".join(r)


# Aliases for compatibility
_bv2av_py = bv2av
_av2bv_py = av2bv
