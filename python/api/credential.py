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
Bilibili credential / authentication.

Minimal drop-in for ``bilibili_api.Credential`` covering only what this
project uses. Cookie management (buvid3/4 auto-generation, bili_ticket)
lives in the requesting module rather than here.
"""

import asyncio
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .exceptions import ArgsException

__all__ = ["Credential", "sync", "ArgsException"]

_EXTRA_COOKIE_NAMES = frozenset({"_uuid", "b_lsid", "b_nut", "bili_ticket", "bili_ticket_expires", "buvid_fp"})


class Credential:
    """
    Auth credential holding Bilibili cookie values.

    Fields correspond to browser cookies:
      - sessdata:       SESSDATA
      - bili_jct:       bili_jct
      - buvid3:         buvid3
      - buvid4:         buvid4
      - dedeuserid:     DedeUserID
      - ac_time_value:  ac_time_value
    """

    def __init__(
        self,
        sessdata=None,
        bili_jct=None,
        buvid3=None,
        buvid4=None,
        dedeuserid=None,
        ac_time_value=None,
        proxy=None,
        **kwargs,
    ):
        self.sessdata = (
            None
            if sessdata is None
            else (sessdata if sessdata.find("%") != -1 else urllib.parse.quote(sessdata))
        )
        self.bili_jct = bili_jct
        self.buvid3 = buvid3
        self.buvid4 = buvid4
        self.dedeuserid = dedeuserid
        self.ac_time_value = ac_time_value
        self.proxy = proxy

        for key, value in kwargs.items():
            setattr(self, key, value)

    def get_cookies(self) -> dict:
        """Return a request cookie dictionary (excluding the proxy field)."""
        cookies = {
            "SESSDATA": self.sessdata if self.sessdata else "",
            "buvid3": self.buvid3 if self.buvid3 else "",
            "buvid4": self.buvid4 if self.buvid4 else "",
            "bili_jct": self.bili_jct if self.bili_jct else "",
            "ac_time_value": self.ac_time_value if self.ac_time_value else "",
        }
        if self.dedeuserid:
            cookies["DedeUserID"] = self.dedeuserid
        for key in _EXTRA_COOKIE_NAMES:
            value = getattr(self, key, None)
            if value is not None:
                cookies[key] = value
        return cookies

    def has_sessdata(self) -> bool:
        return self.sessdata is not None and self.sessdata != ""

    def has_bili_jct(self) -> bool:
        return self.bili_jct is not None and self.bili_jct != ""

    def has_buvid3(self) -> bool:
        return self.buvid3 is not None and self.buvid3 != ""

    def has_buvid4(self) -> bool:
        return self.buvid4 is not None and self.buvid4 != ""

    def has_dedeuserid(self) -> bool:
        return self.dedeuserid is not None and self.dedeuserid != ""

    def has_ac_time_value(self) -> bool:
        return self.ac_time_value is not None and self.ac_time_value != ""

    def raise_for_no_sessdata(self):
        if not self.has_sessdata():
            raise ArgsException("未提供 sessdata 参数")

    def raise_for_no_bili_jct(self):
        if not self.has_bili_jct():
            raise ArgsException("未提供 bili_jct 参数")

    def raise_for_no_buvid3(self):
        if not self.has_buvid3():
            raise ArgsException("未提供 buvid3 参数")

    def raise_for_no_buvid4(self):
        if not self.has_buvid4():
            raise ArgsException("未提供 buvid4 参数")

    def raise_for_no_dedeuserid(self):
        if not self.has_dedeuserid():
            raise ArgsException("未提供 DedeUserID 参数")

    def raise_for_no_ac_time_value(self):
        if not self.has_ac_time_value():
            raise ArgsException("未提供 ac_time_value 参数")

    @staticmethod
    def from_cookies(cookies: dict = None) -> "Credential":
        cookies = cookies or {}
        c = Credential()
        c.sessdata = cookies.get("SESSDATA")
        c.bili_jct = cookies.get("bili_jct")
        c.buvid3 = cookies.get("buvid3")
        c.buvid4 = cookies.get("buvid4")
        c.dedeuserid = cookies.get("DedeUserID")
        c.ac_time_value = cookies.get("ac_time_value")
        for key, value in cookies.items():
            if key not in (
                "SESSDATA",
                "bili_jct",
                "buvid3",
                "buvid4",
                "DedeUserID",
                "ac_time_value",
            ):
                setattr(c, key, value)
        return c

    # --- Cookie refresh (used only by the CLI refresher) ---
    async def check_refresh(self) -> bool:
        """
        Check whether the cookies need refreshing.

        Returns True when Bilibili reports the session should be refreshed.
        Requires the ``pycryptodome`` extra for the refresh flow.
        """
        from .client import get_bili_client, HEADERS

        client = await get_bili_client()
        resp = await client.get(
            "https://passport.bilibili.com/x/passport-login/web/cookie/info",
            cookies=self.get_cookies(),
            headers=HEADERS,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise ArgsException("检查 cookies 是否过期的请求失败")
        return data["data"]["refresh"]

    async def refresh(self) -> None:
        """
        Refresh the cookies in place (updates sessdata, bili_jct,
        dedeuserid and ac_time_value).
        """
        try:
            from Crypto.Cipher import PKCS1_OAEP
            from Crypto.Hash import SHA256
            from Crypto.PublicKey import RSA
        except ImportError:
            raise RuntimeError(
                "Cookie 刷新需要安装 pycryptodome：uv add pycryptodome"
            )

        import binascii
        import time
        import uuid

        from .client import get_bili_client, HEADERS

        self.raise_for_no_bili_jct()
        self.raise_for_no_ac_time_value()

        client = await get_bili_client()
        cookies = self.get_cookies()
        cookies["buvid3"] = str(uuid.uuid1())

        # 1. Refresh CSRF
        rsa = RSA.importKey(
            "-----BEGIN PUBLIC KEY-----\n"
            "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg\n"
            "Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71\n"
            "nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40\n"
            "JNrRuoEUXpabUzGB8QIDAQAB\n"
            "-----END PUBLIC KEY-----"
        )
        ts = round(time.time() * 1000)
        cipher = PKCS1_OAEP.new(rsa, SHA256)
        correspond_path = binascii.b2a_hex(cipher.encrypt(f"refresh_{ts}".encode())).decode()
        resp = await client.get(
            f"https://www.bilibili.com/correspond/1/{correspond_path}",
            cookies=cookies,
            headers=HEADERS,
        )
        if resp.status_code != 200:
            raise RuntimeError("获取刷新 Cookies 的 csrf 失败")
        try:
            refresh_csrf = re.findall('<div id="1-name">(.+?)</div>', resp.text)[0]
        except IndexError:
            raise RuntimeError("correspondPath 过期或错误")

        # 2. Refresh cookies
        data = {
            "csrf": self.bili_jct,
            "refresh_csrf": refresh_csrf,
            "refresh_token": self.ac_time_value,
            "source": "main_web",
        }
        cookies = self.get_cookies()
        cookies["buvid3"] = str(uuid.uuid1())
        resp = await client.post(
            "https://passport.bilibili.com/x/passport-login/web/cookie/refresh",
            cookies=cookies,
            data=data,
            headers=HEADERS,
        )
        if resp.status_code != 200:
            raise RuntimeError("刷新 Cookies 失败")
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError("刷新 Cookies 失败")
        new_credential = Credential(
            sessdata=resp.cookies["SESSDATA"],
            bili_jct=resp.cookies["bili_jct"],
            dedeuserid=resp.cookies["DedeUserID"],
            ac_time_value=body["data"]["refresh_token"],
        )

        # 3. Confirm refresh
        conf_data = {
            "csrf": new_credential.bili_jct,
            "refresh_token": self.ac_time_value,
        }
        await client.post(
            "https://passport.bilibili.com/x/passport-login/web/confirm/refresh",
            cookies=new_credential.get_cookies(),
            data=conf_data,
            headers=HEADERS,
        )

        self.sessdata = new_credential.sessdata
        self.bili_jct = new_credential.bili_jct
        self.dedeuserid = new_credential.dedeuserid
        self.ac_time_value = new_credential.ac_time_value

    def __str__(self):
        return (
            f"SESSDATA: {self.sessdata}; bili_jct: {self.bili_jct}; "
            f"buvid3: {self.buvid3}; buvid4: {self.buvid4}; "
            f"DedeUserID: {self.dedeuserid}; ac_time_value: {self.ac_time_value}"
        )


def _ensure_event_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
            return asyncio.get_event_loop()


def sync(coroutine):
    """
    Run an async coroutine to completion synchronously.

    If called inside a running event loop, runs it in a worker thread
    (mirrors bilibili_api's ``sync`` semantics).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop().run_until_complete(coroutine)
    else:
        with ThreadPoolExecutor() as executor:
            return executor.submit(
                lambda c: asyncio.new_event_loop().run_until_complete(c), coroutine
            ).result()
