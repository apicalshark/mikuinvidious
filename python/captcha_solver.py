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

DEPRECATED / ROUTED OFF: the solver is kept for reference but is no longer
driven. Bilibili's Gaia register answers ``geetest: null`` for the IPs that
actually trip risk control, so a voucher cannot be cleared this way, and the
``gtlv`` dependency has been dropped. ``_auto_solve_enabled`` keeps
``solve_and_update_sec_token`` on the pre-solver route (return ``None`` and let
the upstream risk error surface) without deleting the flow below.
"""

import asyncio
import logging
import time

from fingerprints import get_browser_headers

try:
    import gtlv
except ImportError:  # dependency dropped: solving stays routed off (see _auto_solve_enabled)
    gtlv = None

logger = logging.getLogger(__name__)

_SEC_TOKEN_KEY = "miku:captcha:sec_token"

# get_sec_token runs on every Api.request: bound the Redis backup lookup and
# remember failures so an unreachable Redis cannot tax every Bilibili call.
_REDIS_TOKEN_TIMEOUT = 0.5
_REDIS_MISS_COOLDOWN = 15.0

_mem_sec_token: str | None = None
_mem_sec_token_expiry: float = 0.0
_redis_miss_until: float = 0.0
_DEFAULT_FAILURE_COOLDOWN = 15.0  # seconds to cache solve failure
_last_solve_failure_time = 0.0
_total_solves_count: int = 0
_mem_lock = asyncio.Lock()
_solve_lock = asyncio.Lock()

# Routing switch: solving is off, so requests take the pre-solver path (surface
# the upstream risk error) instead of registering a voucher nobody can clear.
_auto_solve_enabled = False


def get_redis():
    """Return the global Redis client from shared."""
    try:
        from shared import appredis

        return appredis
    except Exception:
        return None


async def get_sec_token() -> str | None:
    """Retrieve the cached X-BILI-SEC-TOKEN if valid.

    Called on every ``Api.request``, so this must stay off the network in the
    common case: the in-memory copy is consulted first and Redis only backs it
    up for a process that has not solved yet (e.g. a second worker). A failed
    or slow lookup is remembered for ``_REDIS_MISS_COOLDOWN`` so a sick Redis
    costs one bounded probe per window instead of a stall on every Bilibili
    call — a 5s socket timeout on every request previously pushed playurl
    resolution past the caller's 8s budget and silently degraded DASH
    playback to the progressive fallback.
    """
    global _redis_miss_until

    now = time.time()
    token = _mem_sec_token
    if token and now < _mem_sec_token_expiry:
        return token

    if now < _redis_miss_until:
        return None

    r = get_redis()
    if r:
        try:
            async with asyncio.timeout(_REDIS_TOKEN_TIMEOUT):
                val = await r.get(_SEC_TOKEN_KEY)
            if val:
                return str(val)
        except Exception:
            _redis_miss_until = now + _REDIS_MISS_COOLDOWN

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
    global _mem_sec_token, _mem_sec_token_expiry, _redis_miss_until
    async with _mem_lock:
        _mem_sec_token = None
        _mem_sec_token_expiry = 0.0
    # A cleared token must re-probe Redis on the next lookup.
    _redis_miss_until = 0.0

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
    global _total_solves_count, _last_solve_failure_time
    if not _auto_solve_enabled or gtlv is None:
        # Routed off: behave exactly like the pre-solver code path.
        return None
    if not v_voucher:
        return None

    now = time.time()
    if not force and now - _last_solve_failure_time < _DEFAULT_FAILURE_COOLDOWN:
        return None

    async with _solve_lock:
        if not force:
            now = time.time()
            if now - _last_solve_failure_time < _DEFAULT_FAILURE_COOLDOWN:
                return None
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

        try:
            async with asyncio.timeout(30.0):
                gt = None
                challenge = None
                token = ""

                # 1. Fetch challenge using v_voucher (single-use token)
                if v_voucher:
                    print(
                        f"[CaptchaSolver] Attempt 1/{max_attempts}: registering Gaia challenge with v_voucher {v_voucher[:16]}..."
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
                            # Raw response logging on attempt 1 for inspection / diagnostics
                            print(f"[CaptchaSolver] Gaia register raw response on attempt 1: {res}")
                            logger.info("[CaptchaSolver] Gaia register raw response on attempt 1: %s", res)

                            if res.get("code") == 0:
                                data = res.get("data") if isinstance(res.get("data"), dict) else {}
                                gt_data = data.get("geetest") if isinstance(data.get("geetest"), dict) else {}
                                v4_data = data.get("geetest_v4") if isinstance(data.get("geetest_v4"), dict) else {}

                                gt = (
                                    gt_data.get("gt")
                                    or data.get("gt")
                                    or v4_data.get("captcha_id")
                                    or data.get("captcha_id")
                                )
                                challenge = (
                                    gt_data.get("challenge") or data.get("challenge") or v4_data.get("challenge")
                                )
                                token = data.get("token") or gt_data.get("token") or v4_data.get("token") or ""

                                captcha_id = v4_data.get("captcha_id") or data.get("captcha_id")
                                if (data.get("type") == "geetest_v4" or v4_data or captcha_id) and not challenge:
                                    print(
                                        f"[CaptchaSolver] Gaia register returned Geetest v4 schema (captcha_id={captcha_id}), which requires Geetest v4 handling: {res}"
                                    )
                                    logger.warning(
                                        "[CaptchaSolver] Gaia register returned Geetest v4 schema (captcha_id=%s): %s",
                                        captcha_id,
                                        res,
                                    )

                                if data.get("geetest") is None and not gt and not challenge:
                                    print(
                                        f"[CaptchaSolver] Gaia register returned null geetest (risk control cannot be cleared via captcha for this voucher/IP): {res}"
                                    )
                                    logger.warning(
                                        "[CaptchaSolver] Gaia register returned null geetest; risk control cannot be cleared via captcha: %s",
                                        res,
                                    )
                                elif gt and challenge:
                                    print(f"[CaptchaSolver] Gaia register succeeded: gt={gt}, challenge={challenge}")
                                    logger.info(
                                        "[CaptchaSolver] Gaia register succeeded: gt=%s, challenge=%s", gt, challenge
                                    )
                                else:
                                    print(
                                        f"[CaptchaSolver] Gaia register succeeded but missing gt/challenge: gt={gt}, challenge={challenge}"
                                    )
                            else:
                                print(f"[CaptchaSolver] Gaia register rejected: {res}")
                                logger.warning("[CaptchaSolver] Gaia register rejected: %s", res)
                        else:
                            print(f"[CaptchaSolver] Gaia register HTTP status {resp.status_code}")
                            logger.warning("[CaptchaSolver] Gaia register HTTP status %s", resp.status_code)
                    except Exception as e:
                        print(f"[CaptchaSolver] Gaia register exception on attempt 1: {e}")
                        logger.warning("[CaptchaSolver] Gaia register exception on attempt 1: %s", e)
                else:
                    print(f"[CaptchaSolver] Attempt 1/{max_attempts}: no v_voucher provided")

                # If registration did not yield a valid challenge, stop immediately.
                # Bilibili vouchers are strictly single-use; retrying the same voucher on
                # attempts 2/3 will fail with error 100000 ("验证码获取失败").
                if not gt or not challenge:
                    print(
                        f"[CaptchaSolver] Unable to acquire gt/challenge from Bilibili (attempt 1/{max_attempts}); "
                        "stopping retry loop because v_voucher is single-use"
                    )
                    _last_solve_failure_time = time.time()
                    return None

                for attempt in range(1, max_attempts + 1):
                    # 2. Solve challenge using gtlv offline neural models
                    print(
                        f"[CaptchaSolver] Solving challenge locally via gtlv (attempt {attempt}/{max_attempts}, gt={gt}, challenge={challenge})..."
                    )
                    try:
                        async with gtlv.Client(max_attempts=1) as solver_client:
                            solution = await solver_client.solve(gt, challenge)
                            print(
                                f"[CaptchaSolver] gtlv solved challenge on attempt {attempt}: validate={solution.validate}"
                            )
                    except Exception as e:
                        print(f"[CaptchaSolver] gtlv solve failed on attempt {attempt}: {e}")
                        continue

                    # 3. Validate solution with Bilibili Gaia gateway
                    print(
                        f"[CaptchaSolver] Submitting validation to Bilibili Gaia gateway (attempt {attempt}/{max_attempts})..."
                    )
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
                    # A validation submission consumes the challenge, so do not retry with the dead challenge
                    break
        except TimeoutError:
            print("[CaptchaSolver] Overall solve timed out after 30s")
        except Exception as e:
            print(f"[CaptchaSolver] Unexpected error during solve loop: {e}")

        _last_solve_failure_time = time.time()
        return None
