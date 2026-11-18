# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for rate limit key generation and route family budgets."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class RateKeys(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_routes_share_family_budgets_and_isolate_clients(self):
        import miku.core.rate_limit as rate_limit
        from miku.app import app
        from miku.core.url_signing import sign_danmaku_url, sign_dash_manifest_url

        limiter = MagicMock()
        limiter.is_allowed = AsyncMock(return_value=(False, {"remaining": 0, "reset": 1, "limit": 10}))
        cases = [
            (["/proxy/download/BV1xx411c7mD/0/80", "/proxy/download/BV1xx411c7mD/1/64"], 10),
            ([sign_danmaku_url("BV1xx411c7mD", 0), sign_danmaku_url("BV1xx411c7mD", 2)], 60),
            (["/video_listen/BV1xx411c7mD", "/video_listen/BV1xx411c7mD:2/"], 60),
            (["/read/cv1", "/opus/2"], 60),
            (["/space/1", "/space/2/"], 60),
            (["/space/1/json", "/space/2/json"], 60),
            (["/author/1", "/author/2/"], 60),
            (["/audio/au1", "/audio/au2"], 60),
            (["/audio_list/am1", "/audio_list/am2:1"], 60),
            (["/vv/1", "/vv/2/"], 60),
            ([sign_dash_manifest_url("BV1xx411c7mD", 0), sign_dash_manifest_url("BV1xx411c7mD", 1)], 60),
        ]
        with (
            patch.dict(rate_limit.appconf["rate_limit"], {"enabled": True}),
            patch.object(rate_limit, "get_rate_limiter", return_value=limiter),
            patch.object(rate_limit, "_peer_is_trusted_proxy", return_value=True),
        ):
            client = app.test_client()
            keys = []
            for paths, limit in cases:
                limiter.is_allowed.reset_mock()
                for path in paths:
                    response = await client.get(path, headers={"X-Real-IP": "203.0.113.1"})
                    self.assertEqual(response.status_code, 429, path)
                calls = limiter.is_allowed.await_args_list
                self.assertEqual(calls[0].args, calls[1].args, paths)
                self.assertEqual(calls[0].args[1:], (limit, 60))
                keys.append(calls[0].args[0])
                await client.get(paths[0], headers={"X-Real-IP": "203.0.113.2"})
                self.assertNotEqual(calls[0].args[0], limiter.is_allowed.await_args.args[0])
            self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
