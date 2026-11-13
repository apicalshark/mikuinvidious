import asyncio
import builtins
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import miku.api as bili_api
from miku.api import client as bili_client
from miku.api import comment, live, search
from miku.api.credential import Credential
from miku.api.exceptions import CookiesRefreshException, NetworkException, ResponseCodeException
from miku.api.video import Video
from miku.api.wbi import _WBI_FALLBACK_TTL
from miku.services.credential import apply_cdn_cred_headers, get_web_api_cookies, is_cred_active


class TestPerLoopClientRegistry(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await bili_client.close_bili_client()

    async def test_per_loop_clients_and_closed_loop_pruning(self):
        created_transports: list[MagicMock] = []

        def fake_transport(proxy, loop=None):
            t = MagicMock()
            t.loop = loop
            t.aclose = AsyncMock()
            t.handle_async_request = AsyncMock(return_value=httpx.Response(200, json={"code": 0}))
            created_transports.append(t)
            return t

        with patch.object(bili_client, "_build_curl_transport", side_effect=fake_transport):
            c1 = await bili_client.get_bili_client()
            c1_again = await bili_client.get_bili_client()
            self.assertIs(c1, c1_again)

            # Second loop gets its own independent client and does not fail on closed loop.
            def run_in_second_loop():
                async def inner():
                    c2 = await bili_client.get_bili_client()
                    self.assertIsNot(c2, c1)
                    return c2

                lp = asyncio.new_event_loop()
                try:
                    c2 = lp.run_until_complete(inner())
                    return c2, lp
                finally:
                    lp.close()

            c2, lp2 = await asyncio.to_thread(run_in_second_loop)
            self.assertTrue(lp2.is_closed())
            self.assertIsNot(c2, c1)

            # Calling get_bili_client() on the current loop prunes the closed loop entry.
            c1_third = await bili_client.get_bili_client()
            self.assertIs(c1_third, c1)
            with bili_client._registry_lock:
                self.assertNotIn(lp2, bili_client._loop_clients)

            await bili_api.close_bili_client()
            self.assertTrue(c1.is_closed)
            # Idempotent when already closed.
            await bili_api.close_bili_client()

    async def test_noop_cookie_policy_keeps_resp_cookies_intact(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers=[("Set-Cookie", "SESSDATA=secret; Path=/"), ("Set-Cookie", "bili_jct=csrf123; Path=/")],
                json={"code": 0},
            )

        transport = httpx.MockTransport(handler)
        with patch.object(bili_client, "_build_curl_transport", return_value=transport):
            client = await bili_client.get_bili_client()
            resp = await client.get("https://api.bilibili.com/x/test")
            self.assertEqual(resp.cookies.get("SESSDATA"), "secret")
            self.assertEqual(resp.cookies.get("bili_jct"), "csrf123")
            self.assertEqual(len(client.cookies.jar), 0)
            # Second request must not emit cookies extracted from the first response.
            seen_cookies: list[str | None] = []

            def inspect_handler(request: httpx.Request) -> httpx.Response:
                seen_cookies.append(request.headers.get("cookie"))
                return httpx.Response(200, json={"code": 0})

            client._transport = httpx.MockTransport(inspect_handler)
            await client.get("https://api.bilibili.com/x/test")
            self.assertIsNone(seen_cookies[0])
            await bili_client.close_bili_client()


class TestApiRequestBehavior(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bili_client.recalculate_wbi()

    def tearDown(self):
        bili_client.recalculate_wbi()

    async def test_json_body_single_encoded(self):
        captured: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.read())
            return httpx.Response(200, json={"code": 0, "data": {"ok": True}})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
                patch.object(bili_client, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b3"})),
            ):
                res = await (
                    bili_client.Api(
                        "https://api.bilibili.com/x/test",
                        "POST",
                        no_csrf=True,
                        json_body=True,
                    )
                    .update_data(hello="world", count=2)
                    .result
                )
            self.assertEqual(res, {"ok": True})
            parsed = json.loads(captured[0].decode("utf-8"))
            self.assertIsInstance(parsed, dict)
            self.assertEqual(parsed, {"hello": "world", "count": 2})
        finally:
            await mock_client.aclose()

    async def test_wbi_wire_percent20_and_url_query_folding(self):
        raw_paths: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            raw_paths.append(request.url.raw_path)
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch.object(bili_client, "get_curl_client", AsyncMock(return_value=mock_client)),
                patch.object(
                    bili_client, "get_wbi_mixin_key", AsyncMock(return_value="0123456789abcdef0123456789abcdef")
                ),
                patch("miku.api.wbi.time.time", return_value=1700000000),
            ):
                res = await (
                    bili_client.Api(
                        "https://api.bilibili.com/x/web-interface/wbi/search/type?existing=hello%20there",
                        "GET",
                        wbi=True,
                        curl=True,
                        send_cookies=False,
                    )
                    .update_params(keyword="miku !'()* live")
                    .result
                )
            self.assertEqual(res, {"items": []})
            raw_query = raw_paths[0].decode("ascii").split("?", 1)[1]
            self.assertIn("existing=hello%20there", raw_query)
            self.assertIn("keyword=miku%20%20live", raw_query)
            self.assertIn("w_rid=", raw_query)
            self.assertNotIn("+", raw_query)
        finally:
            await mock_client.aclose()

    async def test_wbi_signing_failure_fails_loudly(self):
        mock_client = MagicMock()
        mock_client.request = AsyncMock()
        with (
            patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
            patch.object(bili_client, "get_wbi_mixin_key", AsyncMock(return_value="")),
        ):
            with self.assertRaises(ValueError):
                await bili_client.Api("https://api.bilibili.com/x/test", "GET", wbi=True).result
        mock_client.request.assert_not_awaited()

    async def test_wbi_fallback_ttl_and_status_checks(self):
        # Empty/invalid /nav triggers _WBI_FALLBACK_TTL (300s), not 12h.
        def nav_502(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(nav_502))
        try:
            with (
                patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
                patch.object(bili_client, "_get_anonymous_cookies", AsyncMock(return_value={"buvid3": "b3"})),
                patch("miku.api.client.time.time", return_value=1000.0),
            ):
                with self.assertRaises(NetworkException) as ctx:
                    await bili_client._get_mixin_key()
                self.assertEqual(ctx.exception.code, 502)

                key = await bili_client.get_wbi_mixin_key()
                self.assertEqual(len(key), 32)
                self.assertEqual(getattr(bili_client, "__wbi_mixin_key_expires"), 1000.0 + _WBI_FALLBACK_TTL)
        finally:
            await mock_client.aclose()

        # Non-200 on GenWebTicket raises NetworkException.
        def ticket_412(request: httpx.Request) -> httpx.Response:
            return httpx.Response(412, text="banned")

        ticket_client = httpx.AsyncClient(transport=httpx.MockTransport(ticket_412))
        try:
            with patch.object(bili_client, "get_bili_client", AsyncMock(return_value=ticket_client)):
                with self.assertRaises(NetworkException) as ctx:
                    await bili_client._get_bili_ticket()
                self.assertEqual(ctx.exception.code, 412)
        finally:
            await ticket_client.aclose()

    async def test_get_mixin_key_fetches_anon_cookies_when_cred_empty(self):
        seen_cookie_headers: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookie_headers.append(request.headers.get("cookie"))
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084481368485c8e428b3d1f3db9ed.png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/411145e16d424f39af363d595240d7e3.png",
                        }
                    },
                },
            )

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            anon_mock = AsyncMock(return_value={"buvid3": "anon_b3", "bili_ticket": "blocked"})
            with (
                patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
                patch.object(bili_client, "_get_anonymous_cookies", anon_mock),
            ):
                key = await bili_client._get_mixin_key(Credential())
            self.assertEqual(len(key), 32)
            anon_mock.assert_awaited_once()
            self.assertIn("buvid3=anon_b3", seen_cookie_headers[0] or "")
            self.assertNotIn("bili_ticket", seen_cookie_headers[0] or "")
        finally:
            await mock_client.aclose()

    async def test_bili_ticket_never_stored_or_sent_as_web_cookie(self):
        cred = Credential.from_cookies(
            {
                "SESSDATA": "s1",
                "bili_ticket": "should_not_store",
                "bili_ticket_expires": "999999",
                "buvid3": "b3",
            }
        )
        cookies = cred.get_cookies()
        self.assertNotIn("bili_ticket", cookies)
        self.assertNotIn("bili_ticket_expires", cookies)

        cred2 = Credential(sessdata="s1", bili_ticket="nope", bili_ticket_expires="123")
        self.assertNotIn("bili_ticket", cred2.get_cookies())

        seen_cookies: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookies.append(request.headers.get("cookie", ""))
            return httpx.Response(200, json={"code": 0, "data": {}})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
                patch.object(
                    bili_client,
                    "_get_anonymous_cookies",
                    AsyncMock(return_value={"buvid3": "b3", "bili_ticket": "leak"}),
                ),
            ):
                await bili_client.Api(
                    "https://api.bilibili.com/x/test",
                    "GET",
                    headers={"Cookie": "bili_ticket=hdr_leak; foo=bar"},
                    credential=cred,
                ).result
                await Video(bvid="BV17x411w7KC", credential=cred).get_danmaku_xml(cid=12345)
            for hdr in seen_cookies:
                self.assertNotIn("bili_ticket", hdr)
            self.assertIn("foo=bar", seen_cookies[0])
            self.assertIn("SESSDATA=s1", seen_cookies[0])
        finally:
            await mock_client.aclose()

    async def test_search_live_comment_raise_network_exception_412(self):
        seen_cookies: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookies.append(request.headers.get("cookie"))
            return httpx.Response(412, text="Precondition Failed")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch.object(bili_client, "get_curl_client", AsyncMock(return_value=mock_client)),
                patch.object(
                    bili_client, "get_wbi_mixin_key", AsyncMock(return_value="0123456789abcdef0123456789abcdef")
                ),
            ):
                with self.assertRaises(NetworkException) as ctx1:
                    await search.search_by_type("miku", search_type=search.SearchObjectType.VIDEO)
                self.assertEqual(ctx1.exception.code, 412)

                with self.assertRaises(NetworkException) as ctx2:
                    await live.LiveRoom(12345).get_room_info()
                self.assertEqual(ctx2.exception.code, 412)

                with self.assertRaises(NetworkException) as ctx3:
                    await comment.get_comments(170001, comment.CommentResourceType.VIDEO)
                self.assertEqual(ctx3.exception.code, 412)

            self.assertTrue(all(c is None for c in seen_cookies))
        finally:
            await mock_client.aclose()


class TestCredentialRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_missing_pycryptodome_raises_cookies_refresh_exception(self):
        cred = Credential(sessdata="orig_sess", bili_jct="orig_jct", dedeuserid="42", ac_time_value="orig_ac")
        orig_import = builtins.__import__

        def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("Crypto"):
                raise ImportError("No module named 'Crypto'")
            return orig_import(name, globals, locals, fromlist, level)

        with (
            patch.dict(
                sys.modules, {"Crypto": None, "Crypto.Cipher": None, "Crypto.Hash": None, "Crypto.PublicKey": None}
            ),
            patch("builtins.__import__", side_effect=blocked_import),
        ):
            with self.assertRaises(CookiesRefreshException):
                await cred.refresh()

        self.assertEqual(cred.sessdata, "orig_sess")
        self.assertEqual(cred.bili_jct, "orig_jct")
        self.assertEqual(cred.dedeuserid, "42")
        self.assertEqual(cred.ac_time_value, "orig_ac")

    async def test_step3_failure_leaves_credential_unchanged(self, outcome="rejected"):
        cred = Credential(
            sessdata="orig_sess",
            bili_jct="orig_jct",
            buvid3="orig_b3",
            buvid4="orig_b4",
            dedeuserid="42",
            ac_time_value="orig_ac",
        )

        fake_crypto_publickey = SimpleNamespace(RSA=SimpleNamespace(importKey=lambda key: object()))
        fake_crypto_cipher = SimpleNamespace(
            PKCS1_OAEP=SimpleNamespace(new=lambda rsa, sha: SimpleNamespace(encrypt=lambda b: b"cipher"))
        )
        fake_crypto_hash = SimpleNamespace(SHA256=object())

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/correspond/1/" in url:
                return httpx.Response(200, text='<div id="1-name">csrf_tok</div>')
            if url.endswith("/cookie/refresh"):
                return httpx.Response(
                    200,
                    headers=[
                        ("Set-Cookie", "SESSDATA=new_sess; Path=/"),
                        ("Set-Cookie", "bili_jct=new_jct; Path=/"),
                        ("Set-Cookie", "DedeUserID=99; Path=/"),
                    ],
                    json={"code": 0, "data": {"refresh_token": "new_ac"}},
                )
            if url.endswith("/confirm/refresh"):
                if outcome == "transport":
                    raise httpx.ReadError("connection lost")
                if outcome == "http":
                    return httpx.Response(502)
                if outcome == "json":
                    return httpx.Response(200, text="invalid")
                if outcome == "missing":
                    return httpx.Response(200, json={})
                return httpx.Response(200, json={"code": -101, "message": "confirm failed"})
            raise AssertionError(f"Unexpected URL: {url}")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with (
                patch.dict(
                    sys.modules,
                    {
                        "Crypto": SimpleNamespace(),
                        "Crypto.PublicKey": fake_crypto_publickey,
                        "Crypto.Cipher": fake_crypto_cipher,
                        "Crypto.Hash": fake_crypto_hash,
                    },
                ),
                patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)),
            ):
                with self.assertRaises(CookiesRefreshException) as caught:
                    await cred.refresh()
                if outcome != "rejected":
                    self.assertEqual(caught.exception.credential.sessdata, "new_sess")
                    self.assertEqual(caught.exception.credential.ac_time_value, "new_ac")

            # Must remain completely unchanged after Step 3 failure.
            self.assertEqual(cred.sessdata, "orig_sess")
            self.assertEqual(cred.bili_jct, "orig_jct")
            self.assertEqual(cred.dedeuserid, "42")
            self.assertEqual(cred.ac_time_value, "orig_ac")
        finally:
            await mock_client.aclose()

    async def test_unknown_confirmation_retains_candidate(self):
        for outcome in ("transport", "http", "json", "missing"):
            with self.subTest(outcome=outcome):
                await self.test_step3_failure_leaves_credential_unchanged(outcome)

    async def test_check_refresh_status_and_code_checks(self):
        cred = Credential(sessdata="s1")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": -101, "message": "not logged in"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with patch.object(bili_client, "get_bili_client", AsyncMock(return_value=mock_client)):
                with self.assertRaises(ResponseCodeException) as ctx:
                    await cred.check_refresh()
                self.assertEqual(ctx.exception.code, -101)
        finally:
            await mock_client.aclose()


class TestCredentialHelpers(unittest.TestCase):
    def test_credential_helpers_ticket_isolation_and_use_cred_guard(self):
        cred = Credential(
            sessdata="sess%20val",
            bili_jct="jct_val",
            buvid3="b3_val",
            buvid4="b4_val",
            dedeuserid="12345",
            ac_time_value="ac_val",
            bili_ticket="leak_ticket",
            bili_ticket_expires="9999999999",
        )
        disabled_conf = {"credential": {"use_cred": False}}
        enabled_conf = {"credential": {"use_cred": True}}

        self.assertFalse(is_cred_active(None, conf=enabled_conf))
        self.assertFalse(is_cred_active(cred, conf=disabled_conf))
        self.assertTrue(is_cred_active(cred, conf=enabled_conf))

        self.assertEqual(get_web_api_cookies(cred, conf=disabled_conf), {})
        web_cookies = get_web_api_cookies(cred, conf=enabled_conf)
        self.assertNotIn("bili_ticket", web_cookies)
        self.assertNotIn("bili_ticket_expires", web_cookies)
        self.assertEqual(web_cookies["SESSDATA"], "sess%20val")
        self.assertEqual(web_cookies["DedeUserID"], "12345")

        headers = {"User-Agent": "Chrome", "cookie": "stale=1"}
        apply_cdn_cred_headers(headers, cred, ticket="jwt_ticket", conf=enabled_conf)
        self.assertEqual(headers["x-bili-ticket"], "jwt_ticket")
        self.assertEqual(headers["buvid"], "b3_val")
        self.assertEqual(headers["buvid4"], "b4_val")
        self.assertNotIn("cookie", headers)
        self.assertNotIn("bili_ticket=", headers["Cookie"])
        self.assertIn("SESSDATA=sess%20val", headers["Cookie"])
        self.assertIn("DedeUserID=12345", headers["Cookie"])

        inactive_headers = {"User-Agent": "Chrome"}
        apply_cdn_cred_headers(inactive_headers, cred, ticket="jwt_ticket", conf=disabled_conf)
        self.assertEqual(inactive_headers["x-bili-ticket"], "jwt_ticket")
        self.assertNotIn("Cookie", inactive_headers)
        self.assertNotIn("buvid", inactive_headers)


class TestSyncCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_owned_loop_closes_on_success_and_failure_in_both_contexts(self):
        def run():
            for fails in (False, True):
                seen = []

                async def operation(seen=seen, fails=fails):
                    seen.append(asyncio.get_running_loop())
                    if fails:
                        raise ValueError("test")
                    return 42

                async def close(seen=seen):
                    self.assertIs(asyncio.get_running_loop(), seen[0])

                with patch.object(bili_client, "close_bili_client", AsyncMock(side_effect=close)) as cleanup:
                    if fails:
                        with self.assertRaises(ValueError):
                            bili_api.sync(operation())
                    else:
                        self.assertEqual(bili_api.sync(operation()), 42)
                    cleanup.assert_awaited_once()
                self.assertTrue(seen[0].is_closed())

        await asyncio.to_thread(run)
        run()


if __name__ == "__main__":
    unittest.main()
