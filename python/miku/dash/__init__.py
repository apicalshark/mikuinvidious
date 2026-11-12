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
"""DASH proxy facade module.

Imports modularized DASH submodules (dash_common, dash_fetch, dash_mpd,
dash_stream, dash_download_track, dash_download_job) and provides explicit
re-exports for backward compatibility without wildcard namespace pollution.
"""

from __future__ import annotations

import miku.dash.common as dash_common
import miku.dash.download_job as dash_download_job
import miku.dash.download_track as dash_download_track
import miku.dash.fetch as dash_fetch
import miku.dash.mpd as dash_mpd
import miku.dash.stream as dash_stream
from miku.dash.common import (
    dash_proxy_bp,
)
from miku.dash.download_job import (
    DownloadCapacityError,
    cancel_download_job,
    create_download_job,
    proxy_download,
)
from miku.dash.fetch import (
    _select_durl_results,
    fetch_durl_supported_src,
    has_valid_dash_tracks,
    video_get_dash_for_qn,
)
from miku.dash.mpd import generate_vod_mpd
from miku.dash.stream import proxy_dash

__all__ = [
    "DownloadCapacityError",
    "_select_durl_results",
    "cancel_download_job",
    "create_download_job",
    "dash_common",
    "dash_download_job",
    "dash_download_track",
    "dash_fetch",
    "dash_mpd",
    "dash_proxy_bp",
    "dash_stream",
    "fetch_durl_supported_src",
    "generate_vod_mpd",
    "has_valid_dash_tracks",
    "proxy_dash",
    "proxy_download",
    "video_get_dash_for_qn",
]
