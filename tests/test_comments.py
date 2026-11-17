# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for comment component and pagination endpoints."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import miku.api.comment as api_comment
import miku.views as views
import miku.views.video as views_video


class CommentPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_more_returns_503_on_upstream_failure(self):
        with (
            patch("miku.views.video.comment.get_comments", AsyncMock(side_effect=TimeoutError("upstream timeout"))),
            patch("miku.views.video.render_template_with_theme", AsyncMock(return_value="rendered_error_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views_video.api_component_meta_more("BV1xx411c7xx", 0)
                self.assertIsInstance(result, tuple)
                self.assertEqual(result[1], 503)
                self.assertEqual(result[0], "rendered_error_comments")

    async def test_meta_more_returns_200_on_success(self):
        mock_comments = {
            "page": {"count": 1},
            "replies": [{"rpid": 123}],
            "next_offset": "offset2",
            "is_end": False,
        }
        with (
            patch("miku.views.video.comment.get_comments", AsyncMock(return_value=mock_comments)),
            patch("miku.views.video.render_template_with_theme", AsyncMock(return_value="rendered_comments")),
        ):
            async with views.app.test_request_context("/api/component/comments/BV1xx411c7xx/0/more?next=offset1"):
                result = await views_video.api_component_meta_more("BV1xx411c7xx", 0)
                self.assertEqual(result, "rendered_comments")

    async def test_get_comments_keeps_first_response_when_retry_budget_exhausted(self):
        first_data = {
            "cursor": {"all_count": 5, "is_end": True, "pagination_reply": {"next_offset": ""}},
            "top_replies": [{"rpid": 1}],
            "replies": [],
        }
        with (
            patch.object(api_comment, "_ensure_numeric_oid", AsyncMock(return_value=12345)),
            patch.object(api_comment, "_fetch_comment_page", AsyncMock(return_value=first_data)) as mock_fetch,
            patch("miku.api.comment.time.monotonic", side_effect=[0.0, 3.4]),
        ):
            res = await api_comment.get_comments("BV1xx411c7xx", api_comment.CommentResourceType.VIDEO, 1, api_comment.OrderType.LIKE)

        self.assertEqual(mock_fetch.await_count, 1)
        self.assertEqual([r["rpid"] for r in res["replies"]], [1])

    async def test_get_comments_propagates_cancellation_on_guest_retry(self):
        first_data = {
            "cursor": {"all_count": 5, "is_end": True, "pagination_reply": {"next_offset": ""}},
            "top_replies": [],
            "replies": [],
        }
        with (
            patch.object(api_comment, "_ensure_numeric_oid", AsyncMock(return_value=12345)),
            patch.object(
                api_comment,
                "_fetch_comment_page",
                AsyncMock(side_effect=[first_data, asyncio.CancelledError()]),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await api_comment.get_comments("BV1xx411c7xx", api_comment.CommentResourceType.VIDEO, 1, api_comment.OrderType.LIKE)


if __name__ == "__main__":
    unittest.main()
