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
dash_stream, dash_download_track, dash_download_job) and provides a dynamic
synchronizing facade for 100% backward compatibility with existing imports,
app blueprint registration, and unittest mocking.
"""

import sys
import types

import dash_common
import dash_download_job
import dash_download_track
import dash_fetch
import dash_mpd
import dash_stream

_SUBMODULES = (
    dash_common,
    dash_fetch,
    dash_mpd,
    dash_stream,
    dash_download_track,
    dash_download_job,
)


class _DashProxyFacade(types.ModuleType):
    """Module proxy that synchronizes patched attributes across DASH submodules."""

    def __init__(self, name: str, submodules: tuple):
        super().__init__(name)
        self._submodules = submodules

    def __getattr__(self, name: str):
        for m in self._submodules:
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        if name != "_submodules" and hasattr(self, "_submodules"):
            for m in self._submodules:
                if name in m.__dict__:
                    setattr(m, name, value)


_facade = _DashProxyFacade(__name__, _SUBMODULES)
_facade.__file__ = __file__
_facade.__doc__ = __doc__
_facade.__package__ = __package__
_facade.__loader__ = __loader__
_facade.__spec__ = __spec__

# Re-export Blueprint and submodules
_facade.dash_proxy_bp = dash_common.dash_proxy_bp

# Re-export all submodule symbols onto the facade
for _mod in _SUBMODULES:
    for _k, _v in _mod.__dict__.items():
        if not _k.startswith("__"):
            setattr(_facade, _k, _v)

# Expose primary interface symbols directly
dash_proxy_bp = dash_common.dash_proxy_bp
DownloadCapacityError = dash_download_job.DownloadCapacityError
create_download_job = dash_download_job.create_download_job
cancel_download_job = dash_download_job.cancel_download_job
video_get_dash_for_qn = dash_fetch.video_get_dash_for_qn
has_valid_dash_tracks = dash_fetch.has_valid_dash_tracks
fetch_durl_supported_src = dash_fetch.fetch_durl_supported_src
generate_vod_mpd = dash_mpd.generate_vod_mpd
proxy_dash = dash_stream.proxy_dash
proxy_download = dash_download_job.proxy_download

sys.modules[__name__] = _facade
