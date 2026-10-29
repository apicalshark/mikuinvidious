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

Imports all modularized DASH submodules (dash_common, dash_fetch, dash_mpd,
dash_stream, dash_download_track, dash_download_job) and provides static
re-exports for backward compatibility.
"""

import dash_common
import dash_download_job
import dash_download_track
import dash_fetch
import dash_mpd
import dash_stream
from dash_common import *  # noqa: F401, F403
from dash_download_job import *  # noqa: F401, F403
from dash_download_track import *  # noqa: F401, F403
from dash_fetch import *  # noqa: F401, F403
from dash_mpd import *  # noqa: F401, F403
from dash_stream import *  # noqa: F401, F403

_SUBMODULES = (
    dash_common,
    dash_fetch,
    dash_mpd,
    dash_stream,
    dash_download_track,
    dash_download_job,
)

# Expose primary interface symbols directly
dash_proxy_bp = dash_common.dash_proxy_bp
DownloadCapacityError = dash_download_job.DownloadCapacityError
create_download_job = dash_download_job.create_download_job
cancel_download_job = dash_download_job.cancel_download_job
video_get_dash_for_qn = dash_fetch.video_get_dash_for_qn
has_valid_dash_tracks = dash_fetch.has_valid_dash_tracks
fetch_durl_supported_src = dash_fetch.fetch_durl_supported_src
_select_durl_results = dash_fetch._select_durl_results
generate_vod_mpd = dash_mpd.generate_vod_mpd
proxy_dash = dash_stream.proxy_dash
proxy_download = dash_download_job.proxy_download
