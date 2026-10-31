# Copyright (C) 2026 MikuInvidious Team
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
"""Automated Backend GeeTest / Gaia Captcha Solver.

Solves Bilibili GeeTest challenges transparently on the backend using the local
offline neural network model via ``gtlv``, acquiring a verified ``X-BILI-SEC-TOKEN``
without any user interruption or frontend interaction.
"""

import asyncio
import logging
import time

import gtlv
from fingerprints import get_browser_headers

logger = logging.getLogger(__name__)

_SEC_TOKEN_KEY = "miku:captcha:sec_token"

_mem_sec_token: str | None = None
_mem_sec_token_expiry: float = 0.0
_total_solves_count: int = 0
_mem_lock = asyncio.Lock()
_solve_lock = asyncio.Lock()


def get_redis():
    """Return the global Redis client from shared."""
    try:
        from shared import appredis

        return appredis
    except Exception:
        return None


async def get_sec_token() -> str | None:
    """Retrieve the cached X-BILI-SEC-TOKEN if valid."""
    now = time.time()
    r = get_redis()
    if r:
        try:
            val = await r.get(_SEC_TOKEN_KEY)
            if val:
                return str(val)
        except Exception:
            pass

    async with _mem_lock:
        if _mem_sec_token and now < _mem_sec_token_expiry:
            return _mem_sec_token

    return None


async def set_sec_token(token: str, ttl: int = 7200) -> None:
    """Store the verified X-BILI-SEC-TOKEN in Redis and in-memory cache."""
    global _mem_sec_token, _mem_sec_token_expiry
    now = time.time()
    async with _mem_lock:
        _mem_sec_token = token
        _mem_sec_token_expiry = now + ttl

    r = get_redis()
    if r:
        try:
            await r.set(_SEC_TOKEN_KEY, token, ex=ttl)
        except Exception:
            pass


async def clear_sec_token() -> None:
    """Evict the cached X-BILI-SEC-TOKEN."""
    global _mem_sec_token, _mem_sec_token_expiry
    async with _mem_lock:
        _mem_sec_token = None
        _mem_sec_token_expiry = 0.0

    r = get_redis()
    if r:
        try:
            await r.delete(_SEC_TOKEN_KEY)
        except Exception:
            pass


async def solve_and_update_sec_token(
    v_voucher: str | None = None, force: bool = False, max_attempts: int = 3
) -> str | None:
    """Solve an authentic Bilibili GeeTest challenge using gtlv and update the sec_token.

    Uses an asyncio lock so multiple concurrent requests don't duplicate solving work;
    all waiting requests receive the token acquired by the first solver.
    """
    global _total_solves_count
    if not v_voucher:
        return None
    async with _solve_lock:
        if not force:
            cached = await get_sec_token()
            if cached:
                print(f"[CaptchaSolver] Using cached X-BILI-SEC-TOKEN ({cached[:12]}...)")
                return cached

        from api.client import get_curl_client

        try:
            client = await get_curl_client()
        except Exception as e:
            print(f"[CaptchaSolver] Failed to get curl client: {e}")
            logger.warning("[CaptchaSolver] Failed to get curl client: %s", e)
            return None

        headers = get_browser_headers(origin="https://www.bilibili.com", referer="https://www.bilibili.com/")

        for attempt in range(1, max_attempts + 1):
            gt = None
            challenge = None
            token = ""

            # 1. Fetch challenge using v_voucher if available
            if v_voucher:
                print(
                    f"[CaptchaSolver] Attempt {attempt}/{max_attempts}: registering Gaia challenge with v_voucher {v_voucher[:16]}..."
                )
                try:
                    resp = await client.post(
                        "https://api.bilibili.com/x/gaia-vgate/v1/register",
                        data={"v_voucher": v_voucher, "csrf": ""},
                        headers=headers,
                        timeout=8.0,
                    )
                    if resp.status_code == 200:
                        res = resp.json()
                        if res.get("code") == 0:
                            data = res.get("data") or {}
                            gt_data = data.get("geetest") or {}
                            gt = gt_data.get("gt")
                            challenge = gt_data.get("challenge")
                            token = data.get("token") or ""
                            print(f"[CaptchaSolver] Gaia register succeeded: gt={gt}, challenge={challenge}")
                        else:
                            print(f"[CaptchaSolver] Gaia register rejected: {res}")
                    else:
                        print(f"[CaptchaSolver] Gaia register HTTP status {resp.status_code}")
                except Exception as e:
                    print(f"[CaptchaSolver] Gaia register exception on attempt {attempt}: {e}")
            else:
                print(f"[CaptchaSolver] Attempt {attempt}/{max_attempts}: no v_voucher provided")

            if not gt or not challenge:
                print(
                    f"[CaptchaSolver] Unable to acquire gt/challenge from Bilibili (attempt {attempt}/{max_attempts})"
                )
                continue

            # 2. Solve challenge using gtlv offline neural models
            print(f"[CaptchaSolver] Solving challenge locally via gtlv (gt={gt}, challenge={challenge})...")
            try:
                async with gtlv.Client(max_attempts=1) as solver_client:
                    solution = await solver_client.solve(gt, challenge)
                    print(f"[CaptchaSolver] gtlv solved challenge: validate={solution.validate}")
            except Exception as e:
                print(f"[CaptchaSolver] gtlv solve failed on attempt {attempt}: {e}")
                continue

            # 3. Validate solution with Bilibili Gaia gateway
            print("[CaptchaSolver] Submitting validation to Bilibili Gaia gateway...")
            try:
                val_url = "https://api.bilibili.com/x/gaia-vgate/v1/validate"
                payload = {
                    "challenge": solution.challenge,
                    "token": token,
                    "validate": solution.validate,
                    "seccode": solution.seccode,
                    "csrf": "",
                }
                resp = await client.post(val_url, data=payload, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    res = resp.json()
                    if res.get("code") == 0:
                        data = res.get("data") or {}
                        real_token = data.get("token") or data.get("sec_token") or data.get("grisk_id")
                        if real_token:
                            sec_token = str(real_token)
                            await set_sec_token(sec_token, ttl=7200)
                            _total_solves_count += 1
                            log_msg = f"[CaptchaSolver] Successfully solved challenge on attempt {attempt}/{max_attempts} (total solves: {_total_solves_count})"
                            print(log_msg)
                            logger.info(log_msg)
                            return sec_token

                    print(f"[CaptchaSolver] Bilibili validation rejected on attempt {attempt}: {res}")
                else:
                    print(f"[CaptchaSolver] Bilibili validation HTTP {resp.status_code} on attempt {attempt}")
            except Exception as e:
                print(f"[CaptchaSolver] Exception during Gaia validate on attempt {attempt}: {e}")

        return None
