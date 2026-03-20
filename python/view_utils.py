import asyncio

from shared import appredis


def is_valid(res):
    return res is not None and not isinstance(res, Exception)


async def safe_api(coro, timeout=4.0):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return None


async def populate_dash_redis(vid, idx, dash_data):
    """Populate individual DASH segment URLs into Redis for the proxy."""
    if not dash_data or "dash" not in dash_data:
        return
    for mt in ["video", "audio"]:
        tracks = dash_data["dash"].get(mt, [])
        if mt == "audio" and not tracks and "flac" in dash_data["dash"]:
            tracks = dash_data["dash"]["flac"].get("audio", [])
        for item in tracks:
            url = item.get("baseUrl") or item.get("base_url")
            qn = item.get("id")
            cid = item.get("codecid") or 0
            if url and qn is not None:
                await appredis.setex(f"miku_dash_url_{vid}_{idx}_{mt}_{qn}_{cid}", 1800, url)
