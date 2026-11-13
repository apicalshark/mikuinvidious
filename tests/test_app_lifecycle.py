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
"""Tests for Wave 2 app lifecycle shutdown hooks and upstream exception HTTP mapping."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import miku.api.client as api_client
import miku.app as miku_app
import miku.core.dns_cache as dns_cache
import miku.dash.download_job as download_job
import miku.dash.stream as dash_stream
import miku.media.image_cache as image_cache_mod
import miku.state as shared
from miku.api import exceptions
from miku.services.network import Network, close_global_client


class TestUpstreamExceptionHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_response_code_exception_http_mapping_and_sanitization(self):
        cases = [
            (-404, 404),
            (62002, 404),
            (-403, 403),
            (-400, 400),
            (-352, 503),
            (412, 503),
            (-509, 429),
            (429, 429),
            (-1, 502),
            (99999, 502),
            (True, 502),
            (None, 502),
        ]
        render_mock = AsyncMock(return_value="rendered")
        with (
            patch.object(miku_app, "render_template_with_theme", render_mock),
            patch.dict(miku_app.appconf["site"], {"site_show_unsafe_error_response": False}),
        ):
            for code, expected_http in cases:
                render_mock.reset_mock()
                exc = exceptions.ResponseCodeException(code, "SECRET_RAW_UPSTREAM_MSG")
                async with miku_app.app.test_request_context("/"):
                    body, status_code = await miku_app.resp_exception_view(exc)
                self.assertEqual(body, "rendered")
                self.assertEqual(status_code, expected_http, f"code={code!r}")
                kwargs = render_mock.await_args.kwargs
                self.assertNotIn("SECRET_RAW_UPSTREAM_MSG", kwargs["status"])
                self.assertNotIn("SECRET_RAW_UPSTREAM_MSG", kwargs["desc"])
                if code in (-404, 62002):
                    self.assertIsNotNone(kwargs["suggest"])
                else:
                    self.assertIsNone(kwargs["suggest"])

        with (
            patch.object(miku_app, "render_template_with_theme", render_mock),
            patch.dict(miku_app.appconf["site"], {"site_show_unsafe_error_response": True}),
        ):
            render_mock.reset_mock()
            exc = exceptions.ResponseCodeException(-403, "RAW_MSG_VISIBLE")
            async with miku_app.app.test_request_context("/"):
                _, status_code = await miku_app.resp_exception_view(exc)
            self.assertEqual(status_code, 403)
            kwargs = render_mock.await_args.kwargs
            self.assertEqual(kwargs["status"], "RAW_MSG_VISIBLE")
            self.assertIn("RAW_MSG_VISIBLE", kwargs["desc"])

    async def test_network_exception_http_mapping_and_sanitization(self):
        cases = [
            (412, 503),
            (503, 503),
            (429, 429),
            (400, 400),
            (401, 401),
            (403, 403),
            (404, 404),
            (500, 502),
            (999, 502),
        ]
        render_mock = AsyncMock(return_value="net-err")
        with (
            patch.object(miku_app, "render_template_with_theme", render_mock),
            patch.dict(miku_app.appconf["site"], {"site_show_unsafe_error_response": False}),
        ):
            for code, expected_http in cases:
                render_mock.reset_mock()
                exc = exceptions.NetworkException(code, "SENSITIVE_UPSTREAM_BODY")
                async with miku_app.app.test_request_context("/"):
                    body, status_code = await miku_app.network_exception_view(exc)
                self.assertEqual(body, "net-err")
                self.assertEqual(status_code, expected_http, f"code={code!r}")
                kwargs = render_mock.await_args.kwargs
                self.assertNotIn("SENSITIVE_UPSTREAM_BODY", kwargs["status"])
                self.assertNotIn("SENSITIVE_UPSTREAM_BODY", kwargs["desc"])

        with (
            patch.object(miku_app, "render_template_with_theme", render_mock),
            patch.dict(miku_app.appconf["site"], {"site_show_unsafe_error_response": True}),
        ):
            render_mock.reset_mock()
            exc = exceptions.NetworkException(412, "RAW_412_BODY")
            async with miku_app.app.test_request_context("/"):
                _, status_code = await miku_app.network_exception_view(exc)
            self.assertEqual(status_code, 503)
            kwargs = render_mock.await_args.kwargs
            self.assertEqual(kwargs["status"], "RAW_412_BODY")
            self.assertIn("RAW_412_BODY", kwargs["desc"])


class TestShutdownCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cleanup_runs_all_seven_hooks_even_on_failures(self):
        close_global = AsyncMock(side_effect=RuntimeError("global client boom"))
        close_bili = AsyncMock(side_effect=RuntimeError("bili client boom"))
        close_pool = AsyncMock(side_effect=RuntimeError("pool boom"))
        stop_jobs = AsyncMock(side_effect=RuntimeError("jobs boom"))
        close_img = AsyncMock(side_effect=RuntimeError("img boom"))
        clear_dns = MagicMock(side_effect=RuntimeError("dns boom"))
        fake_redis = MagicMock()
        fake_redis.aclose = AsyncMock(side_effect=RuntimeError("redis boom"))

        with (
            patch.object(miku_app, "close_global_client", close_global),
            patch.object(api_client, "close_bili_client", close_bili),
            patch.object(dash_stream.playback_pool, "close_all", close_pool),
            patch.object(download_job, "shutdown_download_jobs", stop_jobs),
            patch.object(image_cache_mod.image_cache, "close", close_img),
            patch.object(dns_cache, "clear_cache", clear_dns),
            patch.object(shared, "appredis", fake_redis),
        ):
            await miku_app.shutdown_cleanup()

        close_global.assert_awaited_once()
        close_bili.assert_awaited_once()
        close_pool.assert_awaited_once()
        stop_jobs.assert_awaited_once()
        close_img.assert_awaited_once()
        clear_dns.assert_called_once()
        fake_redis.aclose.assert_awaited_once()

    async def test_shutdown_cleanup_handles_none_redis(self):
        close_global = AsyncMock()
        close_bili = AsyncMock()
        close_pool = AsyncMock()
        stop_jobs = AsyncMock()
        close_img = AsyncMock()
        clear_dns = MagicMock()

        with (
            patch.object(miku_app, "close_global_client", close_global),
            patch.object(api_client, "close_bili_client", close_bili),
            patch.object(dash_stream.playback_pool, "close_all", close_pool),
            patch.object(download_job, "shutdown_download_jobs", stop_jobs),
            patch.object(image_cache_mod.image_cache, "close", close_img),
            patch.object(dns_cache, "clear_cache", clear_dns),
            patch.object(shared, "appredis", None),
        ):
            await miku_app.shutdown_cleanup()

        close_global.assert_awaited_once()
        close_bili.assert_awaited_once()
        close_pool.assert_awaited_once()
        stop_jobs.assert_awaited_once()
        close_img.assert_awaited_once()
        clear_dns.assert_called_once()


class TestNetworkClientCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_close_global_client_closes_all_three_and_resets_none(self):
        async_client = MagicMock()
        async_client.is_closed = False
        async_client.aclose = AsyncMock(side_effect=RuntimeError("async close failed"))

        image_client = MagicMock()
        image_client.is_closed = False
        image_client.aclose = AsyncMock()

        sync_client = MagicMock()
        sync_client.is_closed = False
        sync_client.close = MagicMock()

        Network._async_client = async_client
        Network._image_client = image_client
        Network._sync_client = sync_client
        try:
            await close_global_client()
            async_client.aclose.assert_awaited_once()
            image_client.aclose.assert_awaited_once()
            sync_client.close.assert_called_once()
            self.assertIsNone(Network._async_client)
            self.assertIsNone(Network._image_client)
            self.assertIsNone(Network._sync_client)

            # Second invocation is a safe no-op
            await close_global_client()
            image_client.aclose.assert_awaited_once()
            sync_client.close.assert_called_once()
        finally:
            Network._async_client = None
            Network._image_client = None
            Network._sync_client = None


if __name__ == "__main__":
    unittest.main()
