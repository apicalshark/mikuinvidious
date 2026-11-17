# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

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
