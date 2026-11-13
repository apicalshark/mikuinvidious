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
and re-exports view functions, helpers, and modules for backward compatibility.
"""

from __future__ import annotations

import miku.views.audio as views_audio
import miku.views.home as views_home
import miku.views.live as views_live
import miku.views.space as views_space
import miku.views.video as views_video
from miku.state import app

_SUBMODULES = (views_home, views_space, views_live, views_video, views_audio)

__all__ = [
    "app",
    "views_audio",
    "views_home",
    "views_live",
    "views_space",
    "views_video",
]


def __getattr__(name: str):
    for mod in _SUBMODULES:
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    names = set(__all__)
    for mod in _SUBMODULES:
        names.update(dir(mod))
    return sorted(names)

