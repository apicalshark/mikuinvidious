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
"""Dummy catalog of backend UI strings for gettext extraction.

Views pass English literals as ``status``/``desc``/``suggest``/``message``
kwargs; :func:`shared.render_template_with_theme` translates them at the
render choke point via ``gettext_msg``. This module is never imported —
it exists so ``pybabel extract`` picks these msgids up into ``messages.pot``.
Upstream/dynamic strings (video titles, ``f"Backend error: {e.msg}"``) are
intentionally absent: ``gettext_msg`` returns unknown msgids unchanged.
"""

_ = lambda s: s  # noqa: E731


def _backend_strings():
    _strings = [  # noqa: F841
        _("Article does not exist or parse error"),
        _("Article not found"),
        _("Audio mode load failed"),
        _("Backend server sent an invalid response"),
        _("Bad request"),
        _("Bangumi load failed"),
        _("Bilibili returned empty result (-404)"),
        _("Failed to fetch the live list. Please try again later."),
        _("Failed to fetch this user's profile. Please try again later."),
        _("Failed to load bangumi info. Please check your network or try again later."),
        _("Failed to load the live room. Please check your network or try again later."),
        _("Failed to resolve the redirect target."),
        _("Failed to resolve the short link. Please check your network connection or proxy settings."),
        _("Internal server error. Please try again later or contact the administrator."),
        _("Invalid request parameters. Please check and try again."),
        _("Live list load failed"),
        _("Live load failed"),
        _("Network error"),
        _("No search keyword provided."),
        _("No songs found"),
        _("Page not found (404)"),
        _("Parse error"),
        _("Playlist empty"),
        _("Please check the URL."),
        _("Please check your network connection or proxy settings."),
        _("Please check your network or try again later."),
        _("Please check your request and try again."),
        _("Please enter a search keyword and try again."),
        _("Please try refreshing the page, or check the server network connection."),
        _("Search failed"),
        _("Server error"),
        _("Space load failed"),
        _("The article you requested most likely does not exist. Please check your request."),
        _("The requested page does not exist."),
        _("The requested resource does not exist."),
        _(
            "This content is usually unavailable in your region or has been removed by Bilibili. "
            "Please try again later."
        ),
        _("This content may be region-restricted or removed."),
        _("This video may have been deleted, is under review, or is region-restricted."),
        _("Video load failed"),
        _("You have no browsing history yet."),
    ]
