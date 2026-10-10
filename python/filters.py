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

from datetime import datetime, timedelta

import transformers
from markupsafe import Markup
from shared import app


# Convert a timestamp to a humand readable date format.
# NOTE: locale-aware formatting comes later; ISO-like default keeps PO msgids stable.
@app.template_filter("date")
def _jinja2_filter_datetime(ts, fmt="%Y-%m-%d %H:%M"):
    # Live rooms (offline / risk-controlled) and other endpoints can yield
    # None/0/"" for timestamps. Never 500 the page on a bad date — render
    # an empty string instead (e.g. /live/1838245893 with no live_start_time).
    if ts is None or ts == "" or ts == 0:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime(fmt)
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


# Convert a integer to the one with separator like 1,000,000.
@app.template_filter("intsep")
def _jinja2_filter_intsep(i):
    try:
        return f"{int(i):,}"
    except (ValueError, TypeError):
        return "0"


# Convert a duration in seconds to human readable duration.
@app.template_filter("secdur")
def __jinja2_filter_secdur(delta_t):
    try:
        return str(timedelta(seconds=int(delta_t)))
    except (ValueError, TypeError):
        return "0:00:00"


# Convert a url of a photo asset to MikuInvidious proxy url.
@app.template_filter("pic")
def __jinja2_filter_pic(url):
    if not url:
        return ""
    if "://" in url:
        return "/proxy/pic/" + url.split("://", 1)[1]
    if url.startswith("//"):
        return "/proxy/pic/" + url[2:]
    return "/proxy/pic/" + url


# Safely format descriptions with paragraphs and breaks.
@app.template_filter("format_desc")
def __jinja2_filter_format_desc(desc):
    return Markup(transformers.format_description(desc))


# Render a comment content dict: escaped text with Bilibili [emote]
# placeholders replaced by same-origin proxied <img> tags. Output is fully
# sanitized by transformers.render_reply_content (no upstream HTML/JS/URLs
# reach the client), so Markup is safe here.
@app.template_filter("reply_content")
def __jinja2_filter_reply_content(content):
    return Markup(transformers.render_reply_content(content))
