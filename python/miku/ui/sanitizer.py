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
"""HTML sanitization for article content and user-submitted markup."""

from __future__ import annotations

import nh3

# Allowed HTML tags and attributes for article content
ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "u",
    "s",
    "code",
    "pre",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "figure",
    "figcaption",
    "div",
    "span",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "sup",
    "sub",
    "mark",
    "del",
    "ins",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "class", "width", "height"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"style", "class"},
    "div": {"style", "class"},
    "p": {"style", "class"},
    "figure": {"style", "class"},
    "blockquote": {"style", "class"},
    "*": {"style", "class"},
}

ALLOWED_STYLES = {
    "color",
    "background-color",
    "font-weight",
    "font-style",
    "text-decoration",
    "text-align",
    "margin",
    "padding",
    "border",
    "width",
    "height",
    "display",
    "vertical-align",
}


def sanitize_html(html: str) -> str:
    """Sanitize HTML to prevent XSS using nh3 (ammonia)."""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        filter_style_properties=ALLOWED_STYLES,
        clean_content_tags={"script", "style"},
        link_rel=None,
    )
