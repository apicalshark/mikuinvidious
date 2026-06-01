import hmac
import hashlib
import time
import asyncio
import os
import warnings

# This file contains the legacy custom implementation of Bilibili ticket fetching.
# It is kept here for reference but is no longer used in favor of the upstream bilibili_api implementation.

class LegacyTicketManager:
    """
    Legacy custom implementation of Bilibili's x-bili-ticket fetching.
    DEPRECATED: Use the implementation in shared.py which bridges to bilibili_api.
    """

    @classmethod
    async def fetch_new_ticket_custom(cls, appconf, Network, COMMON_HEADERS):
        """
        Original custom implementation using Android key (ec01).
        DEPRECATED: No longer used.
        """
        warnings.warn(
            "LegacyTicketManager.fetch_new_ticket_custom is deprecated and not for production use.",
            DeprecationWarning,
            stacklevel=2
        )
        
        key_id = "ec01"
        key = b"Ezlc3tgtl"
        ts = int(time.time())

        # HMAC-SHA256(key, "ts" + ts)
        hexsign = hmac.new(key, f"ts{ts}".encode(), hashlib.sha256).hexdigest()

        url = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
        params = {
            "key_id": key_id,
            "hexsign": hexsign,
            "context[ts]": ts,
            "csrf": appconf["credential"].get("bili_jct", ""),
        }

        cookies = {}
        if appconf["credential"].get("buvid3"):
            cookies["buvid3"] = appconf["credential"]["buvid3"]

        client = await Network.get_async_client()
        try:
            headers = COMMON_HEADERS.copy()
            if appconf["credential"].get("buvid3"):
                headers["buvid"] = appconf["credential"]["buvid3"]
            
            resp = await client.post(url, params=params, cookies=cookies, headers=headers, timeout=10.0)
            data = resp.json()
            if data.get("code") == 0:
                return data["data"]
        except Exception as e:
            print(f"[LegacyTicket] Request failed: {e}")

        return None
