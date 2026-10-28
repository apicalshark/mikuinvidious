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

import asyncio
import datetime

import orjson
from api import article, opus, user
from api.exceptions import is_risk_error
from extra import article_to_any, article_to_html, get_article_info
from quart import Response, request
from shared import (
    Network,
    app,
    appconf,
    appcred,
    cache_get,
    cache_get_stale,
    cache_minutes,
    cache_set,
    render_template_with_theme,
)


def _space_data_key(mid, pn=1):
    # Page-1 payload (space:data:<mid>) is shared with /space/<mid>/json so
    # one Bilibili fetch serves both routes; deeper pages get their own
    # per-page key (space:data:<mid>:<pn>).
    return f"space:data:{mid}" if pn == 1 else f"space:data:{mid}:{pn}"


def _is_good_space_data(uinfo, uvids):
    """Only healthy payloads may be cached: valid profile + non-empty vlist.

    Transient upstream failures surface as exceptions (handled by callers) or
    as valid-looking but empty vlists; caching those would stick the channel
    on "0 videos" until expiry, so they are served live and never stored.
    """
    if not isinstance(uinfo, dict) or not (uinfo.get("name") or uinfo.get("face")):
        return False
    if not isinstance(uvids, dict):
        return False
    lst = uvids.get("list")
    vlist = lst.get("vlist", []) if isinstance(lst, dict) else []
    return isinstance(vlist, list) and len(vlist) > 0


def _max_space_ttl():
    """Redis expiry for the shared key: the longest interested route policy."""
    return max(max(cache_minutes("space_minutes"), cache_minutes("space_json_minutes")), 0) * 60


async def _read_space_data(mid, max_age_seconds, pn=1):
    """Return (uinfo, uvids) if the unified entry is fresh for this reader.

    Each route passes its own TTL as max_age_seconds, so divergent
    SPACE_CACHE_MINUTES / SPACE_JSON_CACHE_MINUTES policies are each
    strictly enforced even though both share one key. Entries written
    before fetched_at existed are treated as expired (self-migrating).
    """
    data = await cache_get(_space_data_key(mid, pn), max_age_seconds)
    if not isinstance(data, dict):
        return None, None
    uinfo, uvids = data.get("uinfo"), data.get("uvids")
    if not _is_good_space_data(uinfo, uvids):
        return None, None
    return uinfo, uvids


async def _read_space_data_stale(mid, max_age_seconds, pn=1):
    """Return (uinfo, uvids, is_stale), including expired-but-intact entries.

    Only entries that pass the same healthy-payload check as fresh reads
    are returned, so stale-serve can never resurrect a broken profile.
    """
    data, is_stale = await cache_get_stale(_space_data_key(mid, pn), max_age_seconds)
    if not isinstance(data, dict):
        return None, None, False
    uinfo, uvids = data.get("uinfo"), data.get("uvids")
    if not _is_good_space_data(uinfo, uvids):
        return None, None, False
    return uinfo, uvids, is_stale


async def _write_space_data(mid, uinfo, uvids, pn=1, ttl=None):
    """Store a healthy payload; page-1 expiry covers the longest route policy."""
    if not _is_good_space_data(uinfo, uvids):
        return
    if ttl is None:
        ttl = _max_space_ttl()
    await cache_set(_space_data_key(mid, pn), {"uinfo": uinfo, "uvids": uvids}, ttl)


async def _fetch_space_data(mid, pn=1, ps=30):
    u = user.User(mid, credential=appcred)
    uinfo, uvids = await asyncio.gather(u.get_user_info(), u.get_videos(pn=pn, ps=ps))
    return uinfo, uvids, bool(getattr(u, "_degraded", False))


def _space_html_response(html, *, use_cache, cache_hit, degraded, stale=False):
    """200 HTML response for space pages with observability headers."""
    headers = {}
    if use_cache:
        headers["X-Cache"] = "STALE" if stale else ("HIT" if cache_hit else "MISS")
    if degraded:
        headers["X-Degraded"] = "risk-control"
    return Response(html, status=200, content_type="text/html", headers=headers)


@app.route("/space/<mid>")
@app.route("/space/<mid>/")
async def space_view(mid):
    try:
        pn = int(request.args.get("i") or 1)
    except TypeError, ValueError:
        pn = 1
    pn = max(pn, 1)
    # Page-1 payload is shared with /space/<mid>/json; deeper pages get
    # their own per-page key. All pages honor SPACE_CACHE_MINUTES.
    cache_ttl = cache_minutes("space_minutes") * 60
    use_cache = cache_ttl > 0
    cache_hit = False
    degraded = False
    load_failed = False
    uinfo = None
    uvids = {}
    if use_cache:
        uinfo, uvids = await _read_space_data(mid, cache_ttl, pn)
        cache_hit = uinfo is not None
    if not cache_hit:
        u = user.User(mid, credential=appcred)
        try:
            # ps=30 on the page-1 path so the payload is a superset the JSON
            # feed can also use; page-1 HTML is sliced back to 28 below.
            uinfo, uvids = await asyncio.gather(u.get_user_info(), u.get_videos(pn=pn, ps=30 if pn == 1 else 28))
        except Exception as e:
            # The user API is often risk-controlled / IP-blocked (412/-352) without the
            # WARP proxy; re-run the core profile fetch alone in case only a sibling
            # gather task failed. The video list is optional and falls back to empty.
            # Only recognized risk-control errors mark the page degraded;
            # anything else is a neutral load failure (see template).
            if is_risk_error(e):
                degraded = True
            else:
                load_failed = True
            uvids = {}
            if not isinstance(uinfo, dict) or not uinfo:
                try:
                    uinfo = await u.get_user_info()
                except Exception:
                    uinfo = None
        degraded = degraded or bool(getattr(u, "_degraded", False))
        load_failed = load_failed or bool(getattr(u, "_videos_load_failed", False))
        if use_cache:
            # Page-1 key expiry covers the longest (space/json) policy so a
            # divergent JSON TTL isn't cut short; deeper pages use their own.
            await _write_space_data(mid, uinfo, uvids, pn, _max_space_ttl() if pn == 1 else cache_ttl)
    stale = False
    if (not isinstance(uinfo, dict) or not uinfo) and use_cache:
        # Upstream profile fetch failed (often 412/-352 risk control without
        # WARP): serve the last good payload instead of 500ing the space.
        suinfo, suvids, is_stale = await _read_space_data_stale(mid, cache_ttl, pn)
        if is_stale and isinstance(suinfo, dict) and suinfo:
            uinfo, uvids, stale = suinfo, suvids if isinstance(suvids, dict) else {}, True
            degraded = True
    if not isinstance(uinfo, dict) or not uinfo:
        return await render_template_with_theme(
            "error.html",
            status="Space load failed",
            desc="Failed to fetch this user's profile. Please try again later.",
            suggest="Please check your network connection or proxy settings.",
        ), 500
    if not isinstance(uvids, dict):
        uvids = {}
    uvids.setdefault("list", {}).setdefault("vlist", [])
    uvids.setdefault("page", {"count": 0, "pn": 1, "ps": 28})
    # Coerce numeric page fields to int so the template's arithmetic/comparisons
    # (e.g. `pn > 1`) never hit str-vs-int errors.
    page = uvids.get("page") or {}
    try:
        page["pn"] = int(page.get("pn", 1))
    except TypeError, ValueError:
        page["pn"] = 1
    try:
        page["ps"] = int(page.get("ps", 28))
    except TypeError, ValueError:
        page["ps"] = 28
    try:
        page["count"] = int(page.get("count", 0))
    except TypeError, ValueError:
        page["count"] = 0
    vlist = uvids.get("list", {}).get("vlist", [])
    if pn == 1 and isinstance(vlist, list) and len(vlist) > 28:
        # Unified payload carries ps=30 for the JSON feed; keep page-1 HTML
        # identical to the classic ps=28 view so pagination stays aligned.
        uvids["list"]["vlist"] = vlist = vlist[:28]
        if isinstance(page, dict):
            page["ps"] = 28
    # The fallback recArchivesByKeywords endpoint has no author/owner name; since
    # this is the user's own space, stamp it from the profile.
    uname = uinfo.get("name", "")
    for v in vlist:
        if not v.get("author") and uname:
            v["author"] = uname
    # Warn only when content is actually missing: a throttle blip that the
    # fallback covered (full list) needs no banner; an empty list after risk
    # control is "throttled", not "this channel has no videos".
    degraded = degraded and not vlist
    load_failed = load_failed and not vlist and not degraded
    if stale:
        # Stale-served data may be outdated: keep the banner on even when
        # the cached list is non-empty so the age is visible to the user.
        degraded = True
    html = await render_template_with_theme(
        "space.html", uinfo=uinfo, uvids=uvids, degraded=degraded, load_failed=load_failed
    )
    return _space_html_response(html, use_cache=use_cache, cache_hit=cache_hit, degraded=degraded, stale=stale)


@app.route("/space/<mid>/json")
async def space_json_feed(mid):
    cache_ttl = cache_minutes("space_json_minutes") * 60
    cache_hit = False
    uinfo = uvids = None
    if cache_ttl > 0:
        # Served from the same unified payload as /space/<mid> (page 1):
        # whichever route misses first pays for the one upstream fetch.
        uinfo, uvids = await _read_space_data(mid, cache_ttl)
        cache_hit = uinfo is not None
    if not cache_hit:
        try:
            uinfo, uvids, degraded = await _fetch_space_data(mid, pn=1, ps=30)
        except Exception as e:
            return Response(
                orjson.dumps({"error": str(e)}),
                status=502,
                content_type="application/json",
            )
    else:
        degraded = False
        if not isinstance(uinfo, dict) or not isinstance(uvids, dict):
            return Response(
                orjson.dumps({"error": "Unexpected response format from Bilibili API"}),
                status=502,
                content_type="application/json",
            )
        if cache_ttl > 0:
            await _write_space_data(mid, uinfo, uvids)

    site_url = appconf["site"]["site_url"]
    feed_url = f"{site_url}/space/{mid}/json"
    home_url = f"{site_url}/space/{mid}"
    face_url = uinfo.get("face", "")

    items = []
    for v in uvids.get("list", {}).get("vlist", []):
        bvid = v.get("bvid", "")
        title = v.get("title", "")
        items.append(
            {
                "id": bvid,
                "url": f"{site_url}/video/{bvid}",
                "external_url": f"https://www.bilibili.com/video/{bvid}",
                "title": title,
                "content_text": title,
                "date_published": datetime.datetime.fromtimestamp(v.get("created", 0), tz=datetime.UTC).isoformat(),
                "image": v.get("pic", ""),
            }
        )

    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"{uinfo.get('name', 'Unknown')}",
        "home_page_url": home_url,
        "feed_url": feed_url,
        "favicon": face_url,
        "description": uinfo.get("sign", ""),
        "items": items,
    }
    if degraded and not items:
        # Machine-readable sibling of the space.html banner: upstream
        # rate-limited us, so an empty item list means "throttled", not
        # "no videos". (Underscore prefix = JSON Feed custom extension.)
        feed["_degraded"] = "risk-control"

    raw = orjson.dumps(feed)
    # Payload caching already happened via _write_space_data above (healthy
    # payloads only); the feed itself is cheaply rebuilt from cached data.
    feed_headers = {"X-Degraded": "risk-control"} if degraded and not items else {}
    if cache_ttl > 0:
        feed_headers["X-Cache"] = "HIT" if cache_hit else "MISS"
    return Response(raw, status=200, content_type="application/feed+json", headers=feed_headers)


@app.route("/author/<mid>")
@app.route("/author/<mid>/")
async def author_view(mid):
    try:
        pn = int(request.args.get("i") or 1)
    except TypeError, ValueError:
        pn = 1
    pn = max(pn, 1)
    # Page-1 payload uses the base key; deeper pages get per-page keys.
    cache_ttl = cache_minutes("author_minutes") * 60
    use_cache = cache_ttl > 0
    cache_key = f"author:data:{mid}" if pn == 1 else f"author:data:{mid}:{pn}"
    cache_hit = False
    uinfo = uarticles = None
    if use_cache:
        data = await cache_get(cache_key, cache_ttl)
        if isinstance(data, dict):
            uinfo, uarticles = data.get("uinfo"), data.get("uarticles")
            cache_hit = isinstance(uinfo, dict) and bool(uinfo) and isinstance(uarticles, dict)
            if not cache_hit:
                uinfo = uarticles = None
    if not cache_hit:
        u = user.User(mid, credential=appcred)
        uinfo, uarticles = await asyncio.gather(u.get_user_info(), u.get_articles(pn=pn, ps=28))
        # Only cache a healthy payload; never stick a broken profile.
        if use_cache and isinstance(uinfo, dict) and uinfo and isinstance(uarticles, dict):
            await cache_set(cache_key, {"uinfo": uinfo, "uarticles": uarticles}, cache_ttl)
    html = await render_template_with_theme("author.html", uinfo=uinfo, uarts=uarticles)
    if use_cache:
        return Response(
            html,
            status=200,
            content_type="text/html",
            headers={"X-Cache": "HIT" if cache_hit else "MISS"},
        )
    return html


@app.route("/read/<cid>")
@app.route("/read/<cid>/")
@app.route("/read/mobile/<cid>")
@app.route("/read/mobile/<cid>/")
@app.route("/opus/<cid>")
@app.route("/opus/<cid>/")
async def read_view(cid):
    is_opus = "opus" in request.path or not cid.startswith("cv")
    url = (
        f"https://www.bilibili.com/opus/{cid.replace('opus', '')}"
        if is_opus
        else f"https://www.bilibili.com/read/{cid}"
    )
    cvid = cid.replace("cv", "").replace("opus", "")
    ua = "Mozilla/5.0 BiliDroid/8.76.0 (bbcallen@gmail.com) 8.76.0 os/android model/WTF mobi_app/android build/8760000 channel/not_found innerVer/8760010 osVer/15 network/2"

    # Optional pandoc export (any supported format).
    want_format = (
        request.args.get("format")
        if (
            appconf["render"]["use_pandoc"]
            and request.args.get("format") in appconf["render"]["article_allowed_formats"]
        )
        else None
    )

    cache_ttl = cache_minutes("article_minutes") * 60
    # File exports (?format=) bypass the cache; the HTML flavor is part of
    # the key so /read/cv.. and /opus/.. never share entries.
    use_cache = want_format is None and cache_ttl > 0
    cache_key = f"read:data:{cid}:{'opus' if is_opus else 'cv'}"
    if use_cache:
        data = await cache_get(cache_key, cache_ttl)
        if (
            isinstance(data, dict)
            and isinstance(data.get("arinfo"), dict)
            and data.get("arinfo")
            and isinstance(data.get("content"), str)
            and data.get("content")
            and "Failed to parse article content" not in data.get("content")
        ):
            html = await render_template_with_theme(
                "read.html",
                cid=cid,
                arinfo=data["arinfo"],
                article_content=data["content"],
                is_opus=is_opus,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    client = await Network.get_async_client()

    # The public read/opus page is intermittently served with the content module
    # stripped (anti-bot) from datacenter IPs, even though HTTP 200 with title.
    # Retry the scrape until the article body actually parses (or give up).
    text = None
    content = None
    last_status = 0
    for attempt in range(3):
        req = await client.send(
            client.build_request("GET", url, headers={"User-Agent": ua}),
            follow_redirects=True,
        )
        status, body = req.status_code, req.text
        await req.aclose()
        last_status = status
        if status != 200:
            await asyncio.sleep(0.4 * (attempt + 1))
            continue
        try:
            parsed = article_to_html(body)
        except Exception:
            parsed = "<p>Failed to parse article content.</p>"
        if want_format is not None or "Failed to parse article content" not in parsed:
            text, content = body, parsed
            break
        await asyncio.sleep(0.4 * (attempt + 1))

    if text is None:
        return await render_template_with_theme(
            "error.html",
            status="Article not found" if last_status == 404 else "Server error",
            desc="Backend server sent an invalid response",
            suggest="The article you requested most likely does not exist. Please check your request."
            if last_status == 404
            else None,
        ), (404 if last_status == 404 else 502)

    if want_format is not None:
        return await article_to_any(text, want_format)

    try:
        arinfo = get_article_info(text, cid)
        try:
            if is_opus:
                o = opus.Opus(int(cvid), credential=appcred)
                api_info = await o.get_info()
                for module in api_info.get("item", {}).get("modules", []):
                    if module.get("module_stat"):
                        stat = module["module_stat"]
                        arinfo["stats"]["like"] = stat.get("like", {}).get("count", arinfo["stats"]["like"])
                        arinfo["stats"]["coin"] = stat.get("coin", {}).get("count", arinfo["stats"]["coin"])
                        arinfo["stats"]["favorite"] = stat.get("favorite", {}).get("count", arinfo["stats"]["favorite"])
                        arinfo["stats"]["share"] = stat.get("forward", {}).get("count", arinfo["stats"]["share"])
            else:
                ar = article.Article(int(cvid))
                api_info = await ar.get_info()
                if api_info.get("stats"):
                    arinfo["stats"].update(api_info["stats"])
                if api_info.get("title") and not arinfo["title"]:
                    arinfo["title"] = api_info["title"]
        except Exception:
            pass
        # Only cache fully parsed articles; stripped anti-bot bodies are
        # retried live next time instead of sticking.
        if (
            use_cache
            and isinstance(arinfo, dict)
            and arinfo
            and isinstance(content, str)
            and content
            and "Failed to parse article content" not in content
        ):
            await cache_set(cache_key, {"arinfo": arinfo, "content": content}, cache_ttl)
        html = await render_template_with_theme(
            "read.html", cid=cid, arinfo=arinfo, article_content=content, is_opus=is_opus
        )
        if use_cache:
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
        return html
    except Exception:
        import traceback

        traceback.print_exc()
        return await render_template_with_theme(
            "error.html",
            status="Article not found",
            desc="Article does not exist or parse error",
            suggest="The article you requested most likely does not exist. Please check your request.",
        ), 404


