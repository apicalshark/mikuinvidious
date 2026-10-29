import unittest

from app import app
from csrf import get_csrf_token, validate_csrf_token
from quart.sessions import SecureCookieSessionInterface


class TestCsrfSession(unittest.IsolatedAsyncioTestCase):
    async def test_session_interface_is_secure_cookie(self):
        """Verify that the default session interface is SecureCookieSessionInterface."""
        self.assertIsInstance(app.session_interface, SecureCookieSessionInterface)

    async def test_csrf_token_lifecycle_with_secure_cookie(self):
        """Verify CSRF token generation and validation across secure cookie session."""
        async with app.test_request_context("/"):
            token = await get_csrf_token()
            self.assertTrue(bool(token))
            self.assertEqual(len(token), 64)
            self.assertTrue(await validate_csrf_token(token))
            self.assertFalse(await validate_csrf_token("invalid_token"))
            self.assertFalse(await validate_csrf_token(""))
