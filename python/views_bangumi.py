import asyncio
import json
import os
import re
from datetime import datetime

import shared
import zhconv
from api import bangumi
from extra import av2bv
from nyaa import search_nyaa
from quart import Blueprint, Response, jsonify, request
from rate_limit import RATE_LIMITS, rate_limit

bangumi_bp = Blueprint("bangumi", __name__, url_prefix="/bangumi")

# Load filter configuration
params_path = os.path.join(os.path.dirname(bangumi.__file__), "data", "bangumi_index_params.json")
try:
    with open(params_path, encoding="utf-8") as f:
        INDEX_PARAMS = json.load(f)
except:
    INDEX_PARAMS = {}


@bangumi_bp.route("/")
async def bangumi_home():
    stype = request.args.get("type", "anime")
    if stype not in INDEX_PARAMS:
        stype = "anime"

    current_params = INDEX_PARAMS[stype]
    filters = {}

    for f_item in current_params.get("filters", []):
        val = request.args.get(f_item["key"], "-1")
        if val.lstrip("-").isdigit():
            filters[f_item["key"]] = int(val)
        else:
            filters[f_item["key"]] = val

    order = request.args.get("order", "3")
    try:
        pn = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        pn = 1

    from api.client import Api

    api_info = bangumi.API["info"]["index"]

    ss_type = int(current_params["ssType"])

    query_params = {
        "type": ss_type,
        "season_type": ss_type,
        "order": int(order) if order.isdigit() else 3,
        "sort": 0,
        "page": pn,
        "pagesize": 20,
    }
    query_params.update(filters)

    try:
        res = await Api(**api_info, credential=shared.appcred).update_params(**query_params).result
    except Exception as e:
        print(f"[Bangumi] Index API Error: {e}")
        res = {"list": [], "has_next": 0}

    return await shared.render_template_with_theme(
        "bangumi_index.html",
        bangumi_list=res.get("list", []),
        has_next=res.get("has_next", 0),
        filter_meta=current_params.get("filters", []),
        order_meta=current_params.get("orders", []),
        current_filters={k: str(v) for k, v in filters.items()},
        current_order=order,
        current_type=stype,
        page=pn,
    )


@bangumi_bp.route("/view/<int:ssid>")
async def bangumi_view(ssid):
    cache_ttl = shared.cache_minutes("bangumi_minutes", 60) * 60
    cache_key = f"bangumi:data:{ssid}"
    cache_hit = False
    meta = episodes = None
    if cache_ttl > 0:
        data = await shared.cache_get(cache_key, cache_ttl)
        if (
            isinstance(data, dict)
            and isinstance(data.get("meta"), dict)
            and data.get("meta")
            and isinstance(data.get("episodes"), list)
        ):
            meta, episodes = data["meta"], data["episodes"]
            cache_hit = True
    if cache_hit:
        html = await shared.render_template_with_theme(
            "bangumi_view.html",
            meta=meta,
            episodes=episodes,
            ssid=ssid,
            nyaa_enabled=shared.appconf["site"]["nyaa_bangumi"],
        )
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "HIT"})
    b = bangumi.Bangumi(ssid=ssid, credential=shared.appcred)
    try:
        # Fetch metadata and episode list directly
        meta_data = await asyncio.wait_for(b.get_meta(), timeout=10.0)
        if not meta_data:
            raise Exception("Bilibili returned no valid bangumi metadata")

        meta = meta_data.get("media", {})
        raw_eps = []

        try:
            eps_data = await b.get_episode_list()
            if eps_data:
                # Iterate over all possible episode locations (standard, collections, extras)
                raw_eps = eps_data.get("main_section", {}).get("episodes", [])
                if not raw_eps:
                    for section in eps_data.get("section", []):
                        raw_eps.extend(section.get("episodes", []))
        except Exception as e_eps:
            print(f"[Bangumi] Warning: Could not fetch episodes for ssid {ssid}: {e_eps}")

        if not raw_eps and meta:
            raw_eps = meta.get("episodes", [])

        if not raw_eps and not meta:
            raise Exception("Bilibili returned empty bangumi data; the content may be removed or region-restricted.")

        episodes = []
        for ep_item in raw_eps:
            episodes.append(
                {
                    "title": ep_item.get("title"),
                    "long_title": ep_item.get("long_title"),
                    "bvid": ep_item.get("bvid") or (av2bv(ep_item.get("aid")) if ep_item.get("aid") else None),
                    "aid": ep_item.get("aid"),
                    "ep_id": ep_item.get("id"),
                }
            )
    except Exception as e:
        # Detail stays server-side (str(e) can embed raw upstream bodies);
        # users get a generic message.
        print(f"[Bangumi] Error fetching ssid {ssid}: {e}")
        return await shared.render_template_with_theme(
            "error.html",
            status="Bangumi load failed",
            desc="Backend server sent an invalid response",
            suggest="This content is usually unavailable in your region or has been removed by Bilibili. Please try again later.",
        )

    # Only cache healthy season payloads; region-blocked/removed titles
    # (empty meta) are served live and never stuck in cache.
    if cache_ttl > 0 and isinstance(meta, dict) and meta:
        await shared.cache_set(cache_key, {"meta": meta, "episodes": episodes}, cache_ttl)

    # Return the base page; Nyaa search moved to the frontend API
    html = await shared.render_template_with_theme(
        "bangumi_view.html",
        meta=meta,
        episodes=episodes,
        ssid=ssid,
        nyaa_enabled=shared.appconf["site"]["nyaa_bangumi"],
    )
    if cache_ttl > 0:
        return Response(html, status=200, content_type="text/html", headers={"X-Cache": "MISS"})
    return html


@bangumi_bp.route("/play/ep<int:ep_id>")
@rate_limit(**RATE_LIMITS["normal"])
async def bangumi_play(ep_id):
    from api.client import Api

    # 1. Fetch Season Info using ep_id
    cred = shared.appcred
    has_sess = cred and cred.sessdata

    api = Api("https://api.bilibili.com/pgc/view/web/season", "GET", verify=(not not has_sess), credential=cred)
    api.params = {"ep_id": ep_id}

    try:
        data = await api.request()
        # Fix: API might return unwrapped result
        res = data.get("result", data)

        # 2. Extract Metadata
        # Find the specific episode
        eps = res.get("episodes", [])
        current_ep = next((e for e in eps if e["id"] == ep_id), None)

        if not current_ep:
            for section in res.get("section", []):
                for ep in section.get("episodes", []):
                    if ep["id"] == ep_id:
                        current_ep = ep
                        break
                if current_ep:
                    break

        if not current_ep:
            raise Exception("Episode not found in season data")

        bvid = current_ep.get("bvid")
        cid = current_ep.get("cid")
        ssid = res.get("season_id")

        # Parse pubdate to timestamp (int)
        pub_time_str = res.get("publish", {}).get("pub_time", "")
        pub_ts = 0
        if pub_time_str:
            try:
                dt = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M:%S")
                pub_ts = int(dt.timestamp())
            except Exception:
                pub_ts = 0

        def safe_int(v, default=0):
            try:
                if v == "--" or v is None:
                    return default
                return int(v)
            except:
                return default

        # Construct vinfo for video.html
        vinfo = {
            "title": f"{res.get('title', '')} - {current_ep.get('title', '')}",
            "desc": res.get("evaluate", "No description"),
            "pic": current_ep.get("cover") or res.get("cover"),
            "owner": {"name": "Bangumi (Official)", "mid": 0, "face": ""},
            "stat": {
                "view": safe_int(res.get("stat", {}).get("views")),
                "like": safe_int(res.get("stat", {}).get("likes")),
                "coin": safe_int(res.get("stat", {}).get("coins")),
                "favorite": safe_int(res.get("stat", {}).get("favorites")),
                "share": safe_int(res.get("stat", {}).get("share")),
            },
            "pubdate": pub_ts,
            "bvid": bvid,
            "cid": cid,
            "duration": safe_int(current_ep.get("duration")),
        }

        # Construct vset (episode list)
        vset = [{"page": 1, "part": current_ep.get("long_title", current_ep.get("title"))}]

        # Related Videos (Other episodes from all sections)
        all_eps = res.get("episodes", []).copy()
        for section in res.get("section", []):
            all_eps.extend(section.get("episodes", []))

        vrelated = []
        for ep in all_eps:
            if ep["id"] == ep_id:
                continue
            vrelated.append(
                {
                    "pic": ep.get("cover"),
                    "title": f"{ep.get('title')} - {ep.get('long_title')}",
                    "owner": {"name": "Bangumi"},
                    "stat": {"view": 0, "danmaku": 0},
                    "duration": ep.get("duration", 0),
                    "bvid": f"ep{ep['id']}",
                }
            )

    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[Bangumi] Play Error: {e}")
        return await shared.render_template_with_theme(
            "error.html",
            status="Bangumi load failed",
            desc="Failed to load bangumi info. Please check your network or try again later.",
            suggest="Please check your network or try again later.",
        )

    return await shared.render_template_with_theme(
        "video.html",
        vid=bvid,
        vinfo=vinfo,
        vcomments={"page": {"count": 0}, "replies": []},
        vrelated=vrelated[:20],
        keywords="",
        supported_src=[],
        ato=False,
        idx=0,
        vset=vset,
        is_live=False,
        ep_id=ep_id,
        ssid=ssid,
    )


@bangumi_bp.route("/api/nyaa/<int:ssid>")
async def bangumi_nyaa_api(ssid):
    if not shared.appconf["site"]["nyaa_bangumi"]:
        return jsonify({"sidebar_html": "", "ep_torrents": {}})

    # 1. Get the title
    b = bangumi.Bangumi(ssid=ssid, credential=shared.appcred)
    meta_data = await asyncio.wait_for(b.get_meta(), timeout=5.0)
    meta = meta_data.get("media", {}) if meta_data else {}
    raw_title = meta.get("title", "")

    if not raw_title:
        return ""

    # 2. Clean up the search query
    raw_title = meta.get("title", "")
    # Strip bracketed content (e.g. region-only tags, season suffixes)
    search_query = re.sub(r"[\(（].*?[\)）]", " ", raw_title)
    # Strip special punctuation, keep spaces
    search_query = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", " ", search_query)
    # Collapse whitespace
    search_query = re.sub(r"\s+", " ", search_query).strip()

    # If the title is too long (over 30 chars), truncate to the first 30 chars to improve match rate
    if len(search_query) > 30:
        simplified_query = search_query[:30].strip()
    else:
        simplified_query = search_query

    # Build Simplified/Traditional Chinese variants
    simplified_query = (
        f"({zhconv.convert(simplified_query, 'zh-hans')})|({zhconv.convert(simplified_query, 'zh-hant')})"
    )
    print(f"[Bangumi] Search Query: {simplified_query}")

    # Build a query with Chinese tags (Nyaa supports parenthesized union search)
    # Add common Chinese tags and well-known Chinese sub groups to ensure Chinese results
    cn_tags = "(CHT|CHS|繁|简|BIG5|喵萌|VCB|LoliHouse|JasinChen|Raws)"
    final_query = f"{simplified_query} {cn_tags}"

    # 3. Run the search
    # First attempt: simplified title + Chinese tags + trusted-only results
    torrents = await search_nyaa(final_query, trusted_only=True, max_pages=3)
    is_fallback = False

    if not torrents:
        # Second attempt: include untrusted results (many sub groups are not Trusted)
        torrents = await search_nyaa(final_query, trusted_only=False, max_pages=3)
        is_fallback = True

    if not torrents and simplified_query != search_query:
        # Third attempt: full title without tags (last-resort fallback)
        torrents = await search_nyaa(search_query, trusted_only=False, max_pages=3)

    # 4. Classification and filtering logic
    def is_chinese_resource(title):
        # 1. Titles containing CJK characters are very likely Chinese resources
        if re.search(r"[\u4e00-\u9fa5]", title):
            return True
        # 2. Check common Chinese tags
        if re.search(r"CHT|CHS|繁|简|BIG5|GB|CHT&CHS|CHS&CHT", title, re.I):
            return True
        # 3. Well-known Chinese sub groups or keywords
        chinese_keywords = [
            "喵萌",
            "VCB",
            "LoliHouse",
            "抽風",
            "千夏",
            "極影",
            "動漫國",
            "漫遊",
            "幻櫻",
            "悠哈",
            "豌豆",
            "風之聖殿",
            "BeanSub",
            "Lilith",
            "ANi",
        ]
        for kw in chinese_keywords:
            if kw.lower() in title.lower():
                return True
        # 4. Exclude resources explicitly tagged as other languages with no Chinese (e.g. Erai-raws multi-language packs)
        # Titles containing [POR-BR], [SPA-LA], [RUS], etc. that failed the checks above count as non-Chinese
        if re.search(r"\[POR-BR\]|\[SPA-LA\]|\[RUS\]|\[FRA\]|\[GER\]", title, re.I):
            return False

        return False

    def extract_episode(title):
        clean_title = re.sub(
            r"10-?bit|Hi10[Pp]?|1080[Pp]|720[Pp]|4[Kk]|2[Kk]|[Hh][. ]?26[45]|[Xx]26[45]|[Vv][Cc][Bb]-?[Ss]tudio|"
            r"[Bb][Dd][Rr]ip|[Ww][Ee][Bb]-?[Dd][Ll]|[Bb]lu-?ray|[Rr]eseed|[Mm]ulti-?[Aa]udio|"
            r"\d{4}年|\d{1,2}月(?:新番|番)|[Hh][Cc]|"
            r"\[\d{4}[.\-/]\d{2}[.\-/]\d{2}\]",
            " ",
            title,
            flags=re.I,
        )
        if re.search(
            r"\[Fin\]|Complete|全[集部]|合集|剧[场場]版|Movie|OVA|SP|\d{1,3}[-~]\d{1,3}|TV\+Movie|TV\+OVA",
            clean_title,
            re.I,
        ):
            if not re.search(r"[Ee](\d{1,3})|第\s?(\d{1,3})\s?[話话]", clean_title):
                return None
        patterns = [
            r"[Ee](\d{1,3})",
            r"第\s?(\d{1,3})\s?[話话]",
            r"\[(\d{1,3})(?:[vV]?\d?.*?|)\]",
            r"\s(\d{1,3})\s",
            r"-\s(\d{1,3})(?!\d)",
            r"(\d{1,3})\.mp4",
        ]
        for p in patterns:
            match = re.search(p, clean_title)
            if match:
                return int(match.group(1))
        return None

    ep_torrents = {}
    collection_torrents = []
    for t in torrents:
        # Filter out non-Chinese resources
        if not is_chinese_resource(t.title):
            continue

        ep_num = extract_episode(t.title)
        if ep_num is not None:
            if ep_num not in ep_torrents:
                ep_torrents[ep_num] = []
            ep_torrents[ep_num].append(t)
        else:
            collection_torrents.append(t)

    sidebar_html = await shared.render_template_with_theme(
        "components/nyaa_sidebar.html",
        collection_torrents=collection_torrents,
        search_query=search_query,
        is_fallback=is_fallback,
    )

    # Format ep_torrents for JSON serialization (Torrent objects to dicts)
    serializable_ep_torrents = {}
    for ep_num, torrents in ep_torrents.items():
        serializable_ep_torrents[ep_num] = [
            {"title": t.title, "magnet": t.magnet_url, "seeders": t.seeders, "leechers": t.leechers, "size": t.size}
            for t in torrents
        ]

    return jsonify({"sidebar_html": sidebar_html, "ep_torrents": serializable_ep_torrents})
