# Copyright (C) 2026 MikuInvidious Team
#
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestCaptchaSolver(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patch get_redis to return None by default so tests don't wait for offline Redis sockets
        self.redis_patch = patch("captcha_solver.get_redis", return_value=None)
        self.redis_patch.start()
        import captcha_solver

        captcha_solver._last_solve_failure_time = 0.0
        await captcha_solver.clear_sec_token()

    async def asyncTearDown(self):
        import captcha_solver

        captcha_solver._last_solve_failure_time = 0.0
        await captcha_solver.clear_sec_token()
        self.redis_patch.stop()

    async def test_token_get_set_clear(self):
        import captcha_solver

        self.assertIsNone(await captcha_solver.get_sec_token())

        await captcha_solver.set_sec_token("test_sec_token_123", ttl=3600)
        self.assertEqual(await captcha_solver.get_sec_token(), "test_sec_token_123")

        await captcha_solver.clear_sec_token()
        self.assertIsNone(await captcha_solver.get_sec_token())

    async def test_token_with_redis(self):
        import captcha_solver

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "redis_token_abc"

        with patch("captcha_solver.get_redis", return_value=mock_redis):
            token = await captcha_solver.get_sec_token()
            self.assertEqual(token, "redis_token_abc")
            mock_redis.get.assert_awaited_once_with("miku:captcha:sec_token")

            await captcha_solver.set_sec_token("new_tok", ttl=3600)
            mock_redis.set.assert_awaited_once_with("miku:captcha:sec_token", "new_tok", ex=3600)

            await captcha_solver.clear_sec_token()
            mock_redis.delete.assert_awaited_once_with("miku:captcha:sec_token")

    async def test_solve_and_update_sec_token_success(self):
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "bili_gaia_token_abc",
                "geetest": {
                    "gt": "test_gt_111",
                    "challenge": "test_challenge_222",
                },
            },
        }

        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "verified_sec_token_xyz",
            },
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.side_effect = [mock_reg_resp, mock_val_resp]

        mock_validation = MagicMock()
        mock_validation.challenge = "solved_challenge_333"
        mock_validation.validate = "validate_444"
        mock_validation.seccode = "validate_444|jordan"

        mock_gtlv_client = AsyncMock()
        mock_gtlv_client.solve.return_value = mock_validation
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="test_voucher_999")
            self.assertEqual(token, "verified_sec_token_xyz")
            self.assertEqual(await captcha_solver.get_sec_token(), "verified_sec_token_xyz")

            # Check register call parameters
            mock_curl_client.post.assert_any_call(
                "https://api.bilibili.com/x/gaia-vgate/v1/register",
                data={"v_voucher": "test_voucher_999", "csrf": ""},
                headers=unittest.mock.ANY,
                timeout=8.0,
            )
            # Check gtlv solver call
            mock_gtlv_client.solve.assert_awaited_once_with("test_gt_111", "test_challenge_222")
            # Check validate call
            mock_curl_client.post.assert_any_call(
                "https://api.bilibili.com/x/gaia-vgate/v1/validate",
                data={
                    "challenge": "solved_challenge_333",
                    "token": "bili_gaia_token_abc",
                    "validate": "validate_444",
                    "seccode": "validate_444|jordan",
                    "csrf": "",
                },
                headers=unittest.mock.ANY,
                timeout=8.0,
            )

    async def test_solve_concurrency_lock_deduplication(self):
        import captcha_solver

        solve_count = 0

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "gaia_tok",
                "geetest": {"gt": "gt1", "challenge": "ch1"},
            },
        }

        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = {
            "code": 0,
            "data": {"token": "concurrent_sec_token"},
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.side_effect = [mock_reg_resp, mock_val_resp]

        mock_validation = MagicMock()
        mock_validation.challenge = "ch_solved"
        mock_validation.validate = "val_solved"
        mock_validation.seccode = "val_solved|jordan"

        async def slow_solve(*args, **kwargs):
            nonlocal solve_count
            solve_count += 1
            await asyncio.sleep(0.05)
            return mock_validation

        mock_gtlv_client = AsyncMock()
        mock_gtlv_client.solve = AsyncMock(side_effect=slow_solve)
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
        ):
            # Fire 3 concurrent solve tasks
            results = await asyncio.gather(
                captcha_solver.solve_and_update_sec_token(v_voucher="v1"),
                captcha_solver.solve_and_update_sec_token(v_voucher="v1"),
                captcha_solver.solve_and_update_sec_token(v_voucher="v1"),
            )

            # All 3 get the token
            for res in results:
                self.assertEqual(res, "concurrent_sec_token")

            # But solve was only executed once!
            self.assertEqual(solve_count, 1)

    async def test_solve_handles_solver_failure(self):
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "tok1",
                "geetest": {"gt": "gt1", "challenge": "ch1"},
            },
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.return_value = mock_reg_resp

        mock_gtlv_client = AsyncMock()
        mock_gtlv_client.solve.side_effect = RuntimeError("gtlv solver exception")
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="voucher_fail")
            self.assertIsNone(token)
            self.assertIsNone(await captcha_solver.get_sec_token())

    async def test_memory_token_short_circuits_redis(self):
        """A live in-memory token must not cost a Redis round trip.

        get_sec_token runs on every Api.request, so a Redis GET here taxes
        every Bilibili call — and an unreachable Redis once added ~4s per
        request, blowing the 8s playurl budget that DASH resolution depends
        on.
        """
        import captcha_solver

        await captcha_solver.set_sec_token("mem_token_1", ttl=3600)

        mock_redis = AsyncMock()
        with patch("captcha_solver.get_redis", return_value=mock_redis):
            self.assertEqual(await captcha_solver.get_sec_token(), "mem_token_1")
            mock_redis.get.assert_not_awaited()

    async def test_broken_redis_probes_at_most_once_per_cooldown(self):
        """A sick Redis costs one bounded probe per window, not one per request."""
        import captcha_solver

        calls = 0

        async def slow_get(*args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(10)  # simulate an unreachable host hanging
            raise OSError("unreachable")

        mock_redis = MagicMock()
        mock_redis.get = slow_get

        with patch("captcha_solver.get_redis", return_value=mock_redis):
            captcha_solver._mem_sec_token = None
            captcha_solver._mem_sec_token_expiry = 0.0
            captcha_solver._redis_miss_until = 0.0

            loop = asyncio.get_running_loop()
            started = loop.time()
            self.assertIsNone(await captcha_solver.get_sec_token())
            first_elapsed = loop.time() - started

            # Bounded by the probe timeout, not by the hung socket.
            self.assertLess(first_elapsed, captcha_solver._REDIS_TOKEN_TIMEOUT + 0.25)

            for _ in range(5):
                started = loop.time()
                self.assertIsNone(await captcha_solver.get_sec_token())
                self.assertLess(loop.time() - started, 0.01)

            self.assertEqual(calls, 1)

    async def test_clear_sec_token_rearms_redis_probe(self):
        import captcha_solver

        captcha_solver._redis_miss_until = 1e12
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "fresh_token"
        with patch("captcha_solver.get_redis", return_value=mock_redis):
            await captcha_solver.clear_sec_token()
            self.assertEqual(await captcha_solver.get_sec_token(), "fresh_token")

    async def test_solve_logs_success_with_attempt_counter(self):
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "tok_attempt",
                "geetest": {"gt": "gt_att", "challenge": "ch_att"},
            },
        }

        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = {
            "code": 0,
            "data": {"token": "sec_attempt_ok"},
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.side_effect = [mock_reg_resp, mock_val_resp]

        mock_validation = MagicMock()
        mock_validation.challenge = "ch_solved"
        mock_validation.validate = "val_solved"
        mock_validation.seccode = "val_solved|jordan"

        mock_gtlv_client = AsyncMock()
        mock_gtlv_client.solve.return_value = mock_validation
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
            patch("builtins.print") as mock_print,
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="v_att")
            self.assertEqual(token, "sec_attempt_ok")

            printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
            has_attempt_log = any("[CaptchaSolver]" in msg and "attempt 1" in msg for msg in printed_messages)
            self.assertTrue(has_attempt_log, f"Expected attempt log in printed messages: {printed_messages}")

    async def test_solve_retries_and_logs_success_attempt_counter(self):
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "tok_att2",
                "geetest": {"gt": "gt_att2", "challenge": "ch_att2"},
            },
        }

        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = {
            "code": 0,
            "data": {"token": "sec_attempt2_ok"},
        }

        mock_curl_client = AsyncMock()
        # Single register call (voucher is single-use), then validate call on successful solve
        mock_curl_client.post.side_effect = [mock_reg_resp, mock_val_resp]

        mock_validation = MagicMock()
        mock_validation.challenge = "ch_solved"
        mock_validation.validate = "val_solved"
        mock_validation.seccode = "val_solved|jordan"

        mock_gtlv_client = AsyncMock()
        # Fail on attempt 1, succeed on attempt 2
        mock_gtlv_client.solve.side_effect = [RuntimeError("temporary error"), mock_validation]
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
            patch("builtins.print") as mock_print,
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="v_att2")
            self.assertEqual(token, "sec_attempt2_ok")

            printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
            has_attempt_log = any("[CaptchaSolver]" in msg and "attempt 2" in msg for msg in printed_messages)
            self.assertTrue(has_attempt_log, f"Expected attempt 2 log in printed messages: {printed_messages}")
            # Ensure register was called exactly once (no repeat attempts with the single-use voucher)
            register_calls = [call for call in mock_curl_client.post.call_args_list if "register" in str(call.args[0])]
            self.assertEqual(len(register_calls), 1)

    async def test_solve_stops_retry_loop_on_register_rejected(self):
        """Single-use vouchers must not be retried when register is rejected (code 100000)."""
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 100000,
            "message": "验证码获取失败",
            "ttl": 1,
            "data": None,
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.return_value = mock_reg_resp

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("builtins.print") as mock_print,
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="voucher_dead_100000")
            self.assertIsNone(token)
            # Register should be called only ONCE, not retried for attempts 2/3
            self.assertEqual(mock_curl_client.post.call_count, 1)

            printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
            self.assertTrue(
                any("single-use" in msg for msg in printed_messages),
                f"Expected single-use notice in printed messages: {printed_messages}",
            )

    async def test_solve_stops_retry_loop_when_geetest_is_null(self):
        """When Bilibili returns null geetest (IP-level blocking), log raw response and stop."""
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        raw_json = {
            "code": 0,
            "message": "0",
            "ttl": 1,
            "data": {
                "type": "geetest",
                "token": "tok_no_gt",
                "geetest": None,
            },
        }
        mock_reg_resp.json.return_value = raw_json

        mock_curl_client = AsyncMock()
        mock_curl_client.post.return_value = mock_reg_resp

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("builtins.print") as mock_print,
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="voucher_null_gt")
            self.assertIsNone(token)
            # Register called only ONCE
            self.assertEqual(mock_curl_client.post.call_count, 1)

            printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
            # Must log raw response on attempt 1
            has_raw_log = any("Gaia register raw response on attempt 1" in msg for msg in printed_messages)
            self.assertTrue(has_raw_log, f"Expected raw response log in: {printed_messages}")
            # Must log null geetest explanation
            has_null_gt_log = any("null geetest" in msg for msg in printed_messages)
            self.assertTrue(has_null_gt_log, f"Expected null geetest log in: {printed_messages}")
            # Must log single-use stop
            has_single_use = any("single-use" in msg for msg in printed_messages)
            self.assertTrue(has_single_use, f"Expected single-use stop in: {printed_messages}")

    async def test_solve_supports_flat_geetest_fields(self):
        """Extraction succeeds when gt and challenge are flat in data."""
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "token": "tok_flat",
                "gt": "gt_flat_123",
                "challenge": "ch_flat_456",
            },
        }

        mock_val_resp = MagicMock()
        mock_val_resp.status_code = 200
        mock_val_resp.json.return_value = {
            "code": 0,
            "data": {"token": "sec_token_flat_ok"},
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.side_effect = [mock_reg_resp, mock_val_resp]

        mock_validation = MagicMock()
        mock_validation.challenge = "ch_solved"
        mock_validation.validate = "val_solved"
        mock_validation.seccode = "val_solved|jordan"

        mock_gtlv_client = AsyncMock()
        mock_gtlv_client.solve.return_value = mock_validation
        mock_gtlv_client.__aenter__.return_value = mock_gtlv_client
        mock_gtlv_client.__aexit__.return_value = None

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("captcha_solver.gtlv.Client", return_value=mock_gtlv_client),
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="voucher_flat")
            self.assertEqual(token, "sec_token_flat_ok")
            mock_gtlv_client.solve.assert_awaited_once_with("gt_flat_123", "ch_flat_456")

    async def test_solve_detects_geetest_v4_schema(self):
        """When Geetest v4 is returned without challenge, log schema and do not retry."""
        import captcha_solver

        mock_reg_resp = MagicMock()
        mock_reg_resp.status_code = 200
        mock_reg_resp.json.return_value = {
            "code": 0,
            "data": {
                "type": "geetest_v4",
                "token": "tok_v4",
                "geetest_v4": {
                    "captcha_id": "v4_captcha_id_789",
                },
            },
        }

        mock_curl_client = AsyncMock()
        mock_curl_client.post.return_value = mock_reg_resp

        with (
            patch("api.client.get_curl_client", AsyncMock(return_value=mock_curl_client)),
            patch("builtins.print") as mock_print,
        ):
            token = await captcha_solver.solve_and_update_sec_token(v_voucher="voucher_v4")
            self.assertIsNone(token)
            self.assertEqual(mock_curl_client.post.call_count, 1)

            printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
            has_v4_log = any("Geetest v4 schema" in msg for msg in printed_messages)
            self.assertTrue(has_v4_log, f"Expected v4 schema log in: {printed_messages}")


if __name__ == "__main__":
    unittest.main()
