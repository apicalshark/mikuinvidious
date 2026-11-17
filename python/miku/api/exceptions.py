# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Bilibili API exception types.

A minimal drop-in for ``bilibili_api.exceptions`` covering only the
exceptions this project actually uses.
"""


class ApiException(Exception):
    """Base exception for all Bilibili API errors."""

    def __init__(self, msg: str = "An error occurred without a specific reason."):
        super().__init__(msg)
        self.msg = msg

    def __str__(self):
        return self.msg


class ArgsException(ApiException):
    """Invalid request/function arguments."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class ResponseCodeException(ApiException):
    """Bilibili API returned a non-zero ``code`` field."""

    def __init__(self, code: int, msg: str, raw: dict = None):
        super().__init__(msg)
        self.msg = msg
        self.code = code
        self.raw = raw

    def __str__(self):
        return f"API returned error code: {self.code}, message: {self.msg}.\n{self.raw}"


class NetworkException(ApiException):
    """HTTP-layer failure (non-200 status, etc.)."""

    def __init__(self, code: int, msg: str = ""):
        super().__init__(msg)
        self.code = code
        self.msg = msg

    def __str__(self):
        return f"Network error: HTTP status code {self.code}"


class CookiesRefreshException(ApiException):
    """Failed to refresh Bilibili cookies."""

    pass


# Risk-control signals worth surfacing (not silently swallowing): Bilibili
# answers enumeration throttling with these instead of data. Verified live:
# rapid space calls return HTTP 412 ("request was banned") even for real
# browsers; -352/-509/-799 are the sibling gates.
RISK_RESPONSE_CODES = frozenset({-352, -412, -509, -799})
RISK_HTTP_STATUS = frozenset({412, 429})


def is_risk_error(exc: Exception) -> bool:
    """True when *exc* is a recognized Bilibili risk-control response."""
    if isinstance(exc, ResponseCodeException):
        return exc.code in RISK_RESPONSE_CODES
    if isinstance(exc, NetworkException):
        return exc.code in RISK_HTTP_STATUS
    return False
