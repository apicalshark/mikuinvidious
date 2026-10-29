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

# Re-export core modules and utilities
import asyncio  # noqa: F401

import views_audio
import views_home
import views_live
import views_space
import views_video
from api import (  # noqa: F401
    article,
    audio,
    comment,
    homepage,
    live,
    live_area,
    opus,
    search,
    user,
    video,
    video_zone,
)
from b23_resolver import extract_b23_or_bili_url, resolve_b23_target  # noqa: F401
from shared import (  # noqa: F401
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
from views_audio import *  # noqa: F401, F403
from views_home import *  # noqa: F401, F403
from views_live import *  # noqa: F401, F403
from views_space import *  # noqa: F401, F403
from views_video import *  # noqa: F401, F403

_SUBMODULES = (views_home, views_space, views_live, views_video, views_audio)
