# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, timedelta

from markupsafe import Markup

import miku.media.transformers as transformers
from miku.core.url_signing import (
    build_signed_pic_url,
    sign_danmaku_url,
    sign_subtitle_vtt_url,
)
from miku.state import app


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
    except ValueError, TypeError, OverflowError, OSError:
        return ""


# Convert a integer to the one with separator like 1,000,000.
@app.template_filter("intsep")
def _jinja2_filter_intsep(i):
    try:
        return f"{int(i):,}"
    except ValueError, TypeError:
        return "0"


# Convert a duration in seconds to human readable duration.
@app.template_filter("secdur")
def __jinja2_filter_secdur(delta_t):
    try:
        return str(timedelta(seconds=int(delta_t)))
    except ValueError, TypeError:
        return "0:00:00"


# Convert a url of a photo asset to MikuInvidious proxy url.
@app.template_filter("pic")
def __jinja2_filter_pic(url):
    return build_signed_pic_url(url)


@app.template_filter("danmaku_url")
def __jinja2_filter_danmaku_url(target):
    if not target or not isinstance(target, str):
        return ""
    if ":" in target:
        vid, raw_idx = target.split(":", 1)
        try:
            idx = int(raw_idx)
        except ValueError:
            idx = 0
    else:
        vid, idx = target, 0
    return sign_danmaku_url(vid, idx)


@app.template_filter("subtitle_url")
def __jinja2_filter_subtitle_url(target):
    if not target or not isinstance(target, str):
        return ""
    parts = target.split(":", 2)
    if len(parts) != 3:
        return ""
    vid, raw_idx, lan = parts
    try:
        idx = int(raw_idx)
    except ValueError:
        return ""
    return sign_subtitle_vtt_url(vid, idx, lan)


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


@app.template_filter("reply_pictures")
def __jinja2_filter_reply_pictures(content):
    return transformers.extract_reply_pictures(content)


@app.template_filter("ugc_season")
def __jinja2_filter_ugc_season(vinfo):
    return transformers.extract_ugc_season(vinfo)
