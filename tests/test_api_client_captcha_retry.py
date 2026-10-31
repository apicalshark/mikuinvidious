# Copyright (C) 2026 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from api.client import Api
from api.exceptions import ResponseCodeException


class TestApiClientCaptchaRetry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.anon_patch = patch("api.client._get_anonymous_cookies", AsyncMock(return_value={}))
        self.anon_patch.start()

    async def asyncTearDown(self):
        self.anon_patch.stop()

    async def test_api_attaches_sec_token(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = {"code": 0, "data": {"message": "ok"}}

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_resp

        with (
            patch("api.client.get_bili_client", AsyncMock(return_value=mock_client)),
            patch("captcha_solver.get_sec_token", AsyncMock(return_value="sec_token_999")),
        ):
            api = Api(url="https://api.bilibili.com/x/test", method="GET")
            data = await api.request()
            self.assertEqual(data, {"message": "ok"})

            # Verify request headers & cookies attached token
            call_kwargs = mock_client.request.call_args.kwargs
            headers = call_kwargs.get("headers", {})
            self.assertEqual(headers.get("x-bili-gaia-vtoken"), "sec_token_999")
            self.assertIn("X-BILI-SEC-TOKEN=sec_token_999", headers.get("Cookie", ""))

    async def test_api_transparent_retry_on_352_code(self):
        # 1st response: -352 risk control with v_voucher
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 200
        mock_resp_fail.headers = {}
        mock_resp_fail.json.return_value = {
            "code": -352,
            "message": "risk control",
            "data": {"v_voucher": "voucher_abc"},
        }

        # 2nd response: successful data after token acquired
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.headers = {}
        mock_resp_success.json.return_value = {
            "code": 0,
            "data": {"list": ["videoA", "videoB"]},
        }

        mock_client = AsyncMock()
        mock_client.request.side_effect = [mock_resp_fail, mock_resp_success]

        # First call to get_sec_token returns None; after solve it returns new token
        mock_tokens = [None, "new_sec_token_777"]

        async def fake_get_token():
            if mock_tokens:
                return mock_tokens.pop(0)
            return "new_sec_token_777"

        mock_solve = AsyncMock(return_value="new_sec_token_777")

        with (
            patch("api.client.get_bili_client", AsyncMock(return_value=mock_client)),
            patch("captcha_solver.get_sec_token", side_effect=fake_get_token),
            patch("captcha_solver.solve_and_update_sec_token", mock_solve),
        ):
            api = Api(url="https://api.bilibili.com/x/space/wbi/arc/search", method="GET")
            result = await api.request()

            # Caller gets clean data transparently!
            self.assertEqual(result, {"list": ["videoA", "videoB"]})

            # Solver was triggered with the voucher
            mock_solve.assert_awaited_once_with(v_voucher="voucher_abc")

            # Client made 2 requests (original + retry)
            self.assertEqual(mock_client.request.call_count, 2)

    async def test_api_transparent_retry_on_v_voucher_header(self):
        # 1st response: code 0 but with x-bili-gaia-vvoucher header
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 200
        mock_resp_fail.headers = {"x-bili-gaia-vvoucher": "voucher_from_header"}
        mock_resp_fail.json.return_value = {
            "code": 0,
            "data": {"v_voucher": "voucher_from_header"},
        }

        # 2nd response: clean data
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.headers = {}
        mock_resp_success.json.return_value = {
            "code": 0,
            "data": {"items": [1, 2, 3]},
        }

        mock_client = AsyncMock()
        mock_client.request.side_effect = [mock_resp_fail, mock_resp_success]

        mock_solve = AsyncMock(return_value="fresh_tok")

        with (
            patch("api.client.get_bili_client", AsyncMock(return_value=mock_client)),
            patch("captcha_solver.get_sec_token", AsyncMock(return_value=None)),
            patch("captcha_solver.solve_and_update_sec_token", mock_solve),
        ):
            api = Api(url="https://api.bilibili.com/x/search", method="GET")
            result = await api.request()

            self.assertEqual(result, {"items": [1, 2, 3]})
            mock_solve.assert_awaited_once_with(v_voucher="voucher_from_header")
            self.assertEqual(mock_client.request.call_count, 2)

    async def test_api_transparent_retry_on_412_http_status(self):
        # 1st response: HTTP 412 with x-bili-gaia-vvoucher header
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 412
        mock_resp_fail.headers = {"x-bili-gaia-vvoucher": "voucher_412"}
        mock_resp_fail.text = "Precondition Failed"
        mock_resp_fail.json.side_effect = Exception("Not JSON")

        # 2nd response: HTTP 200 clean data
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.headers = {}
        mock_resp_success.json.return_value = {
            "code": 0,
            "data": {"list": ["video412_A", "video412_B"]},
        }

        mock_client = AsyncMock()
        mock_client.request.side_effect = [mock_resp_fail, mock_resp_success]

        mock_solve = AsyncMock(return_value="sec_token_412")

        with (
            patch("api.client.get_bili_client", AsyncMock(return_value=mock_client)),
            patch("captcha_solver.get_sec_token", AsyncMock(return_value=None)),
            patch("captcha_solver.solve_and_update_sec_token", mock_solve),
        ):
            api = Api(url="https://api.bilibili.com/x/space/wbi/arc/search", method="GET")
            result = await api.request()

            self.assertEqual(result, {"list": ["video412_A", "video412_B"]})
            mock_solve.assert_awaited_once_with(v_voucher="voucher_412")
            self.assertEqual(mock_client.request.call_count, 2)

    async def test_api_raises_when_solve_fails(self):
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 200
        mock_resp_fail.headers = {}
        mock_resp_fail.json.return_value = {
            "code": -352,
            "message": "blocked",
            "data": {},
        }

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_resp_fail

        mock_solve = AsyncMock(return_value=None)

        with (
            patch("api.client.get_bili_client", AsyncMock(return_value=mock_client)),
            patch("captcha_solver.get_sec_token", AsyncMock(return_value=None)),
            patch("captcha_solver.solve_and_update_sec_token", mock_solve),
        ):
            api = Api(url="https://api.bilibili.com/x/test", method="GET")
            with self.assertRaises(ResponseCodeException) as ctx:
                await api.request()
            self.assertEqual(ctx.exception.code, -352)


if __name__ == "__main__":
    unittest.main()
