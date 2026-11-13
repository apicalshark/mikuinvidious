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
"""Unit tests for DURL fallback and progressive stream selection."""

import unittest

from miku.dash.fetch import _select_durl_results


def durl_result(actual, requested, description):
    return {"quality": actual, "_requested_qn": requested, "new_description": description, "ext": ".mp4"}


class DurlSelectionTests(unittest.TestCase):
    def test_mixed_results_keep_distinct_qualities_and_prefer_exact(self):
        for exact_first in (True, False):
            with self.subTest(exact_first=exact_first):
                exact = durl_result(64, 64, "720p exact")
                duplicate = durl_result(64, 80, "1080p requested")
                results = [exact, duplicate] if exact_first else [duplicate, exact]
                results.extend([durl_result(32, 16, "360p requested"), None])
                picked = _select_durl_results(
                    results, {"support_formats": [{"quality": "32", "display_desc": "480p"}]}
                )
                self.assertEqual([r["quality"] for r in picked], [64, 32])
                self.assertIs(picked[0], exact)
                self.assertEqual([r["new_description"] for r in picked], ["720p exact", "480p"])
                self.assertTrue(all("_requested_qn" not in r for r in picked))

    def test_all_mismatches_keep_single_best_with_malformed_formats(self):
        picked = _select_durl_results(
            [durl_result(32, 16, "360p"), durl_result(64, 80, "1080p")],
            {
                "support_formats": [
                    None,
                    "bad",
                    42,
                    [],
                    {},
                    {"quality": "invalid", "new_description": "bad"},
                    {"quality": "64", "new_description": "720p", "display_desc": "alternate"},
                ]
            },
        )
        self.assertEqual(picked, [{"quality": 64, "new_description": "720p", "ext": ".mp4"}])

    def test_missing_description_preserves_existing_fallback(self):
        self.assertEqual(
            _select_durl_results([durl_result(32, 80, "existing")], None),
            [{"quality": 32, "new_description": "existing", "ext": ".mp4"}],
        )

    def test_empty_results(self):
        self.assertEqual(_select_durl_results([None], None), [])


if __name__ == "__main__":
    unittest.main()
