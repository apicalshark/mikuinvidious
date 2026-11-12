# Copyright (C) 2023 MikuInvidious Team
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
"""Unit tests for comment component and pagination endpoints."""

import unittest
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()
