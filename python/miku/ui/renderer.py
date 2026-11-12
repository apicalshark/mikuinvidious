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
"""Theme and localization template rendering engine."""

from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from quart import current_app, render_template, request

_THEME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

PRESET_ACCENT_MAP = {
    "sakura": "#a85383",
    "bili_pink": "#fb7299",
    "bili_blue": "#00aeec",
    "miku": "#39c5bb",
    "orange": "#ff7a00",
    "lavender": "#795290",
    "ocean": "#2563eb",
}


def detect_theme(default_theme: str = "modern", root_path: str | None = None) -> str:
    """Determine the theme of the user's request.

    Falls back to 'modern' if the requested theme contains invalid characters,
    traversal sequences, or if the directory does not exist.
    """
    import miku.state as shared

    raw_theme = (
        request.args.get("theme")
        or request.cookies.get("theme")
        or shared.appconf.get("display", {}).get("default_theme", default_theme)
    )
    if not raw_theme or not _THEME_RE.match(raw_theme):
        return "modern"

    if root_path is not None:
        theme_path = os.path.join(root_path, "../templates/themes", raw_theme)
    elif current_app and current_app.template_folder:
        theme_path = os.path.join(current_app.template_folder, "themes", raw_theme)
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        theme_path = str(repo_root / "templates" / "themes" / raw_theme)

    if not os.path.isdir(theme_path):
        return "modern"
    return raw_theme


def detect_locale(default_locale: str = "en") -> str:
    """Resolve UI locale: ?lang= > lang cookie > Accept-Language > default."""
    import miku.state as shared
    from miku.ui.i18n import detect_locale as _detect

    def_loc = shared.appconf.get("display", {}).get("default_locale", default_locale)
    return _detect(
        request.args.get("lang"),
        request.cookies.get("lang"),
        request.headers.get("Accept-Language"),
        def_loc,
    )


async def render_template_with_theme(fp: str, **kwargs: Any) -> str:
    """Render a template with theming, accent color, and localization support."""
    import miku.state as shared
    from miku.ui.i18n import get_json_catalog, gettext_msg, ngettext_msg

    t = detect_theme()
    locale = detect_locale()
    _ = lambda s: gettext_msg(locale, s)  # noqa: E731

    # Translate standard UI response messages
    for key in ("status", "desc", "suggest", "message"):
        val = kwargs.get(key)
        if isinstance(val, str):
            kwargs[key] = gettext_msg(locale, val)

    repo_url = shared.appconf.get("site", {}).get(
        "site_modified_source_code_url"
    ) or "https://0xacab.org/johnxina/mikuinvidious"
    commit_url = shared._compute_commit_url(repo_url, shared.GIT_COMMIT)

    cookie_accent = None
    try:
        raw_cookie = request.cookies.get("m3_seed")
        if raw_cookie:
            unquoted = urllib.parse.unquote(raw_cookie).strip()
            if unquoted in PRESET_ACCENT_MAP:
                cookie_accent = PRESET_ACCENT_MAP[unquoted]
            elif unquoted.startswith("#") and len(unquoted) in (4, 7, 9):
                cookie_accent = unquoted
    except Exception:
        pass

    site_default_accent = shared.appconf.get("display", {}).get("default_accent", "#a85383")
    render_fn = getattr(shared, "render_template", render_template)

    return await render_fn(
        f"themes/{t}/{fp}",
        proxy_status=shared.appconf.get("proxy", {}),
        locale=locale,
        asset_version=shared.ASSET_VERSION,
        app_version=shared.APP_VERSION,
        git_commit=shared.GIT_COMMIT,
        commit_url=commit_url,
        default_accent=site_default_accent,
        user_accent=cookie_accent,
        _=_,
        gettext=lambda s: gettext_msg(locale, s),
        ngettext=lambda s, p, n: ngettext_msg(locale, s, p, n),
        i18n_catalog=get_json_catalog(locale),
        **shared.appconf.get("site", {}),
        **kwargs,
    )
