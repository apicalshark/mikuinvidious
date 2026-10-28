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

"""Views facade module.

Imports all modularized view submodules (views_home, views_space, views_live,
views_video, views_audio) to register their routes on the Quart application,
and re-exports all view functions, helpers, and modules for full backward
compatibility with existing imports and test monkeypatching.
"""

import asyncio
import sys
import types

import views_audio
import views_home
import views_live
import views_space
import views_video
from api import article, audio, comment, homepage, live, live_area, opus, search, user, video, video_zone
from b23_resolver import extract_b23_or_bili_url, resolve_b23_target
from shared import (
    Network,
    app,
    appconf,
    appcred,
    appredis,
    cache_get,
    cache_get_stale,
    cache_minutes,
    cache_set,
    render_template_with_theme,
    safe_json_loads,
)

_SUBMODULES = (views_home, views_space, views_live, views_video, views_audio)


class _ViewsFacade(types.ModuleType):
    """Module proxy that synchronizes patched attributes across submodules."""

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


_facade = _ViewsFacade(__name__, _SUBMODULES)

# Re-export core modules and utilities
_facade.app = app
_facade.appconf = appconf
_facade.appcred = appcred
_facade.appredis = appredis
_facade.asyncio = asyncio
_facade.Network = Network
_facade.cache_get = cache_get
_facade.cache_get_stale = cache_get_stale
_facade.cache_minutes = cache_minutes
_facade.cache_set = cache_set
_facade.render_template_with_theme = render_template_with_theme
_facade.safe_json_loads = safe_json_loads
_facade.resolve_b23_target = resolve_b23_target
_facade.extract_b23_or_bili_url = extract_b23_or_bili_url

# Re-export API packages
_facade.article = article
_facade.audio = audio
_facade.comment = comment
_facade.homepage = homepage
_facade.live = live
_facade.live_area = live_area
_facade.opus = opus
_facade.search = search
_facade.user = user
_facade.video = video
_facade.video_zone = video_zone

# Re-export all view functions and submodule symbols
for _mod in _SUBMODULES:
    for _k, _v in _mod.__dict__.items():
        if not _k.startswith("__"):
            setattr(_facade, _k, _v)

sys.modules[__name__] = _facade
