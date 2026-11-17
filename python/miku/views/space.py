# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import datetime
import re
import urllib.parse
import uuid

import orjson
from quart import Response, redirect, request, url_for

from miku.api import article, comment, opus, user
from miku.api.client import _get_anonymous_cookies, get_wbi_mixin_key
from miku.api.exceptions import is_risk_error
from miku.api.wbi import enc_wbi
from miku.core.fingerprints import CHROME_UA, get_article_scrape_headers
from miku.core.rate_limit import RATE_LIMITS, ip_family_key, rate_limit
from miku.media.extra import (
    article_to_any,
    article_to_html,
    get_article_info,
    merge_article_viewinfo,
    merge_opus_modules_info,
    opus_detail_to_arinfo,
    opus_modules_to_html,
)
from miku.state import (
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
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("space"))
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
    uvids.setdefault("page", {"count": 0, "pn": pn, "ps": 28})
    # Coerce numeric page fields to int so the template's arithmetic/comparisons
    # (e.g. `pn > 1`) never hit str-vs-int errors.
    page = uvids.get("page") or {}
    try:
        page["pn"] = int(page.get("pn", pn))
    except TypeError, ValueError:
        page["pn"] = pn
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
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("space_json"))
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
            print(f"[Space] Error fetching space data for {mid}: {e}")
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


async def _enrich_author_articles_with_opus(u, uarticles: dict, pn: int, first_opus_res, article_ttl: int) -> None:
    """Attach opus_id to creator space articles and warm cv->opus cache entries."""
    articles = uarticles.get("articles") if isinstance(uarticles, dict) else None
    if not isinstance(articles, list) or not articles:
        return
    opus_items = []
    cur = first_opus_res
    if isinstance(cur, dict):
        opus_items.extend(cur.get("items") or [])
        for _ in range(1, min(pn, 5)):
            if not cur.get("has_more") or not cur.get("offset"):
                break
            try:
                cur = await u.get_opus_articles(offset=cur["offset"])
            except Exception:
                break
            if not isinstance(cur, dict):
                break
            opus_items.extend(cur.get("items") or [])

    title_to_opus: dict[str, str] = {}
    for item in opus_items:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("opus_id") or "").strip()
        title = str(item.get("content") or "").strip()
        if oid.isdigit() and title and title not in title_to_opus:
            title_to_opus[title] = oid

    warm_tasks = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        matched = title_to_opus.get(str(art.get("title") or "").strip())
        if matched:
            art["opus_id"] = matched
            if article_ttl > 0 and art.get("id"):
                warm_tasks.append(cache_set(f"cv:opus:{art['id']}", {"opus_id": matched}, article_ttl))
    if warm_tasks:
        await asyncio.gather(*warm_tasks, return_exceptions=True)


@app.route("/author/<mid>")
@app.route("/author/<mid>/")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("author"))
async def author_view(mid):
    try:
        pn = int(request.args.get("i") or 1)
    except TypeError, ValueError:
        pn = 1
    pn = max(pn, 1)
    # Page-1 payload uses the base key; deeper pages get per-page keys.
    cache_ttl = cache_minutes("author_minutes") * 60
    use_cache = cache_ttl > 0
    cache_key = f"author:data:v2:{mid}" if pn == 1 else f"author:data:v2:{mid}:{pn}"
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
        opus_coro = u.get_opus_articles() if pn <= 5 else asyncio.sleep(0, result=None)
        uinfo, uarticles, first_opus = await asyncio.gather(
            u.get_user_info(),
            u.get_articles(pn=pn, ps=20),
            opus_coro,
            return_exceptions=True,
        )
        if isinstance(uinfo, BaseException):
            raise uinfo
        if isinstance(uarticles, BaseException):
            raise uarticles
        article_ttl = cache_minutes("article_minutes") * 60
        await _enrich_author_articles_with_opus(u, uarticles, pn, first_opus, article_ttl)
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


async def _fetch_article_comments(arinfo: dict, cvid: str, is_opus: bool) -> tuple[dict, str, int, bool]:
    comment_oid = str(arinfo.get("comment_id") or cvid)
    default_type = (
        comment.CommentResourceType.DYNAMIC_DRAW.value if is_opus else comment.CommentResourceType.ARTICLE.value
    )
    comment_type = arinfo.get("comment_type") or default_type
    comments_closed = False
    raw = None
    try:
        raw = await comment.get_comments(comment_oid, comment_type, 1, comment.OrderType.LIKE, timeout=3.5)
    except Exception as exc:
        comments_closed = getattr(exc, "code", None) == comment.COMMENTS_CLOSED_CODE
    empty = {"page": {"count": 0}, "replies": [], "next_offset": "", "is_end": True}
    vcomments = raw if isinstance(raw, dict) else empty
    return vcomments, comment_oid, comment_type, comments_closed


async def _resolve_cv_opus_id(client, cvid: str, cookie_header: str) -> str | None:
    api_headers = {
        "User-Agent": CHROME_UA,
        "Referer": f"https://www.bilibili.com/read/cv{cvid}/",
        "Origin": "https://www.bilibili.com",
        "Cookie": cookie_header,
    }
    params: dict[str, object] = {"id": cvid, "gaia_source": "main_web", "web_location": "333.976"}
    try:
        mixin = await get_wbi_mixin_key()
        if mixin:
            params = enc_wbi(params, mixin)
    except Exception:
        params = {"id": cvid}
    target_url = f"https://api.bilibili.com/x/article/view?{urllib.parse.urlencode(params)}"

    saw_throttle = False
    for attempt in range(2):
        try:
            api_req = await client.send(client.build_request("GET", target_url, headers=api_headers))
            api_json = orjson.loads(api_req.text) if api_req.status_code == 200 else None
            await api_req.aclose()
            if isinstance(api_json, dict):
                data = api_json.get("data")
                if isinstance(data, dict):
                    dyn_id = data.get("dyn_id_str") or (data.get("opus") or {}).get("opus_id")
                    if dyn_id is not None and str(dyn_id).isdigit():
                        return str(dyn_id)
                if api_json.get("code") in (-509, -352):
                    saw_throttle = True
                    if attempt == 0:
                        await asyncio.sleep(0.35)
                        continue
            break
        except Exception:
            break

    if saw_throttle:
        try:
            vinfo = await article.Article(int(cvid)).get_info()
            mid = vinfo.get("mid") if isinstance(vinfo, dict) else None
            title = str(vinfo.get("title") or "").strip() if isinstance(vinfo, dict) else ""
            if mid and title:
                opus_res = await user.User(mid, credential=appcred).get_opus_articles()
                for item in (opus_res or {}).get("items") or []:
                    if isinstance(item, dict) and str(item.get("content") or "").strip() == title:
                        oid = str(item.get("opus_id") or "").strip()
                        if oid.isdigit():
                            return oid
        except Exception:
            pass
    return None


@app.route("/opus/<cid>/", endpoint="opus_view_slash")
@app.route("/opus/<cid>", endpoint="opus_view")
@app.route("/read/mobile/<cid>/", endpoint="read_mobile_view_slash")
@app.route("/read/mobile/<cid>", endpoint="read_mobile_view")
@app.route("/read/<cid>/")
@app.route("/read/<cid>")
@rate_limit(**RATE_LIMITS["normal"], key_func=ip_family_key("article"))
async def read_view(cid):
    is_opus = request.path.lower().startswith("/opus/")
    if is_opus:
        if not cid.isdigit():
            return await render_template_with_theme(
                "error.html",
                status="Article not found",
                desc="Invalid opus ID",
                suggest="Please check the opus URL and try again.",
            ), 404
        cvid = cid
        url = f"https://www.bilibili.com/opus/{cvid}"
    else:
        norm_cid = cid.lower()
        if norm_cid.startswith("cv"):
            cvid = norm_cid[2:]
        elif request.path.lower().startswith("/read/mobile/") and norm_cid.isdigit():
            cvid = norm_cid
        else:
            cvid = ""
        if not cvid.isdigit():
            return await render_template_with_theme(
                "error.html",
                status="Article not found",
                desc="Invalid article ID",
                suggest="Please check the article URL and try again.",
            ), 404
        url = f"https://www.bilibili.com/read/cv{cvid}/"
    resolved_opus_id = cvid if is_opus else None
    scrape_headers = dict(get_article_scrape_headers(appconf.get("bili")))
    scrape_headers["User-Agent"] = CHROME_UA
    scrape_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    scrape_headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.8"
    cookie_parts = {}
    try:
        anon = await _get_anonymous_cookies()
        if isinstance(anon, dict):
            cookie_parts.update({k: v for k, v in anon.items() if v})
    except Exception:
        pass
    if getattr(appcred, "buvid3", None):
        cookie_parts["buvid3"] = appcred.buvid3
    if not cookie_parts.get("buvid3"):
        cookie_parts["buvid3"] = f"{str(uuid.uuid4()).upper()}infoc"
    scrape_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_parts.items())

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
    cache_key = f"read:data:v2:{cid}:{'opus' if is_opus else 'cv'}"
    opus_map_key = f"cv:opus:{cvid}"
    if not is_opus and cache_ttl > 0:
        cached_opus = await cache_get(opus_map_key, cache_ttl)
        mapped_opus = cached_opus.get("opus_id") if isinstance(cached_opus, dict) else None
        if isinstance(mapped_opus, str) and mapped_opus.isdigit():
            if want_format is None:
                return redirect(url_for("opus_view", cid=mapped_opus))
            resolved_opus_id = mapped_opus
            url = f"https://www.bilibili.com/opus/{mapped_opus}"
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
            vcomments, comment_oid, comment_type, comments_closed = await _fetch_article_comments(
                data["arinfo"], cvid, is_opus
            )
            opus_id_out = data["arinfo"].get("opus_id") or resolved_opus_id
            html = await render_template_with_theme(
                "read.html",
                cid=cid,
                arinfo=data["arinfo"],
                article_content=data["content"],
                is_opus=is_opus,
                opus_id=opus_id_out,
                vcomments=vcomments,
                comment_oid=comment_oid,
                comment_type=comment_type,
                comments_closed=comments_closed,
            )
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})

    arinfo = None
    text = None
    content = None
    last_status = 0
    last_error = None
    attempts_made = 0
    max_attempts = 5
    client = await Network.get_async_client()
    opus_lookup_done = False
    if not is_opus and not resolved_opus_id:
        opus_lookup_done = True
        dyn_id = await _resolve_cv_opus_id(client, cvid, scrape_headers["Cookie"])
        if dyn_id:
            if cache_ttl > 0:
                await cache_set(opus_map_key, {"opus_id": dyn_id}, cache_ttl)
            if want_format is None:
                return redirect(url_for("opus_view", cid=dyn_id))
            resolved_opus_id = dyn_id
            url = f"https://www.bilibili.com/opus/{dyn_id}"

    # The public read/opus page is intermittently served with the content module
    # stripped (anti-bot) from datacenter IPs, even though HTTP 200 with title.
    # Retry the scrape until the article body actually parses (or give up).
    for attempt in range(max_attempts):
        attempts_made = attempt + 1
        try:
            req = await client.send(
                client.build_request("GET", url, headers=scrape_headers),
                follow_redirects=True,
            )
            status, body = req.status_code, req.text
            await req.aclose()
            last_status = status
            if status == 404:
                last_error = "HTTP 404 Not Found"
                print(f"[Article] {cid} ({url}) returned HTTP 404 Not Found")
                break
            if status != 200:
                last_error = f"HTTP {status}"
                print(f"[Article] Attempt {attempts_made}/{max_attempts} for {cid} ({url}) returned HTTP {status}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.6 * (attempt + 1))
                continue
            try:
                parsed = article_to_html(body)
            except Exception as e:
                parsed = "<p>Failed to parse article content.</p>"
                last_error = f"Parse exception: {e}"
                print(f"[Article] Attempt {attempts_made}/{max_attempts} for {cid} parse failed: {e}")
            if want_format is not None or "Failed to parse article content" not in parsed:
                text, content = body, parsed
                if not is_opus:
                    if m_opus := re.search(r"/opus/(\d+)", str(getattr(req, "url", ""))):
                        resolved_opus_id = m_opus.group(1)
                        if cache_ttl > 0:
                            await cache_set(opus_map_key, {"opus_id": resolved_opus_id}, cache_ttl)
                        if want_format is None:
                            return redirect(url_for("opus_view", cid=resolved_opus_id))
                break
            last_error = "Content module missing or unparseable"
            print(
                f"[Article] Attempt {attempts_made}/{max_attempts} for {cid} ({url}) stripped or unparseable content (length={len(body)})"
            )
            if not is_opus and not opus_lookup_done and "article-web-app" in body:
                opus_lookup_done = True
                dyn_id = await _resolve_cv_opus_id(client, cvid, scrape_headers["Cookie"])
                if dyn_id:
                    if cache_ttl > 0:
                        await cache_set(opus_map_key, {"opus_id": dyn_id}, cache_ttl)
                    if want_format is None:
                        return redirect(url_for("opus_view", cid=dyn_id))
                    resolved_opus_id = dyn_id
                    url = f"https://www.bilibili.com/opus/{dyn_id}"
                    opus_req = await client.send(
                        client.build_request("GET", url, headers=scrape_headers),
                        follow_redirects=True,
                    )
                    opus_status, opus_body = opus_req.status_code, opus_req.text
                    await opus_req.aclose()
                    last_status = opus_status
                    if opus_status == 200:
                        try:
                            opus_parsed = article_to_html(opus_body)
                        except Exception as e:
                            opus_parsed = "<p>Failed to parse article content.</p>"
                            last_error = f"Parse exception: {e}"
                        if want_format is not None or "Failed to parse article content" not in opus_parsed:
                            text, content = opus_body, opus_parsed
                            break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"[Article] Attempt {attempts_made}/{max_attempts} for {cid} ({url}) network error: {e}")
        if attempt < max_attempts - 1:
            await asyncio.sleep(0.6 * (attempt + 1))

    if text is None and is_opus and want_format is None and last_status != 404:
        try:
            o = opus.Opus(int(cvid), credential=appcred)
            api_info = await o.get_info()
            item = api_info.get("item") if isinstance(api_info, dict) else None
            if isinstance(item, dict):
                json_html = opus_modules_to_html(item.get("modules") or [])
                if json_html:
                    arinfo = opus_detail_to_arinfo(item)
                    content = json_html
                    rid_str = str((item.get("basic") or {}).get("rid_str") or "")
                    if rid_str.isdigit():
                        try:
                            view_info = await article.Article(int(rid_str)).get_info()
                            merge_article_viewinfo(arinfo, view_info)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Article] Opus JSON API fallback failed for {cid}: {e}")

    if content is None:
        print(
            f"[Article] Failed to fetch article {cid} ({url}) after {attempts_made} attempt(s) "
            f"(last_status={last_status}, last_error={last_error})"
        )
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
        if arinfo is None:
            arinfo = get_article_info(text, cid)
            try:
                if is_opus:
                    o = opus.Opus(int(cvid), credential=appcred)
                    api_info = await o.get_info()
                    merge_opus_modules_info(arinfo, api_info.get("item", {}).get("modules", []))
                else:
                    ar = article.Article(int(cvid))
                    api_info = await ar.get_info()
                    merge_article_viewinfo(arinfo, api_info)
            except Exception as e:
                print(f"[Article] Supplementary metadata lookup failed for {cid}: {e}")
        author_mid = (arinfo.get("author") or {}).get("mid") if isinstance(arinfo, dict) else None
        if author_mid:
            try:
                async with asyncio.timeout(2.5):
                    uinfo = await user.User(author_mid, credential=appcred).get_user_info()
                if isinstance(uinfo, dict) and uinfo:
                    arinfo["author"].setdefault("pendant", uinfo.get("pendant"))
                    arinfo["author"].setdefault("vip", uinfo.get("vip"))
                    if uinfo.get("level") is not None:
                        arinfo["author"]["level"] = uinfo.get("level")
                    elif isinstance(uinfo.get("level_info"), dict):
                        arinfo["author"].setdefault("level_info", uinfo.get("level_info"))
                    if uinfo.get("is_senior_member"):
                        arinfo["author"]["is_senior_member"] = uinfo.get("is_senior_member")
                    if uinfo.get("ip_location"):
                        arinfo["author"]["ip_location"] = uinfo.get("ip_location")
            except Exception:
                pass
        if resolved_opus_id and isinstance(arinfo, dict):
            arinfo["opus_id"] = resolved_opus_id
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
        vcomments, comment_oid, comment_type, comments_closed = await _fetch_article_comments(arinfo, cvid, is_opus)
        html = await render_template_with_theme(
            "read.html",
            cid=cid,
            arinfo=arinfo,
            article_content=content,
            is_opus=is_opus,
            opus_id=resolved_opus_id,
            vcomments=vcomments,
            comment_oid=comment_oid,
            comment_type=comment_type,
            comments_closed=comments_closed,
        )
        if use_cache:
            return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
        return html
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[Article] Error processing {cid}: {e}")
        return await render_template_with_theme(
            "error.html",
            status="Article not found",
            desc="Article does not exist or parse error",
            suggest="The article you requested most likely does not exist. Please check your request.",
        ), 404
