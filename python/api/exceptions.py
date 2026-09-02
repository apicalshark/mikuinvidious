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
"""
Bilibili API exception types.

A minimal drop-in for ``bilibili_api.exceptions`` covering only the
exceptions this project actually uses.
"""


class ApiException(Exception):
    """Base exception for all Bilibili API errors."""

    def __init__(self, msg: str = "出现了错误，但是未说明具体原因。"):
        super().__init__(msg)
        self.msg = msg

    def __str__(self):
        return self.msg


class ArgsException(ApiException):
    """Invalid request/function arguments."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class ResponseCodeException(ApiException):
    """Bilibili API returned a non-zero ``code`` field."""

    def __init__(self, code: int, msg: str, raw: dict = None):
        super().__init__(msg)
        self.msg = msg
        self.code = code
        self.raw = raw

    def __str__(self):
        return f"接口返回错误代码：{self.code}，信息：{self.msg}。\n{self.raw}"


class NetworkException(ApiException):
    """HTTP-layer failure (non-200 status, etc.)."""

    def __init__(self, code: int, msg: str = ""):
        super().__init__(msg)
        self.code = code
        self.msg = msg

    def __str__(self):
        return f"网络错误：HTTP 状态码 {self.code}"


class CookiesRefreshException(ApiException):
    """Failed to refresh Bilibili cookies."""

    pass
