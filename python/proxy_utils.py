from shared import COMMON_HEADERS, appconf, appredis


def get_cookie_jar():
    creds = appconf["credential"]
    return {k: v for k, v in creds.items() if k != "use_cred" and v} if creds["use_cred"] else {}


def get_forwarded_headers(request_headers):
    headers = COMMON_HEADERS.copy()
    for k, v in request_headers.items():
        if k.lower() in ["range", "if-range", "x-playback-session-id"]:
            headers[k.lower()] = v
    return headers


async def get_target_url(req_path):
    if req_path.startswith("/proxy/video/"):
        try:
            parts = req_path.removeprefix("/proxy/video/").split("_")
            if len(parts) < 3:
                return None, None
            vid, vidx, vqn = parts[0], parts[1], parts[2]
            url = await appredis.get(f"mikuinv_{vid}_{vidx}_{vqn}")
            return url, (vid, vidx, vqn)
        except (ValueError, IndexError):
            return None, None
    elif req_path.startswith("/proxy/live/"):
        try:
            parts = req_path.removeprefix("/proxy/live/").split("?")[0].split("_")
            room_id = parts[0]
            vqn = parts[1] if len(parts) > 1 else "default"
            redis_key = f"miku_live_{room_id}_{vqn}" if vqn != "default" else f"miku_live_{room_id}"
            url = await appredis.get(redis_key)
            if not url and vqn == "default":
                fallback_keys = await appredis.keys(f"miku_live_{room_id}_*")
                if fallback_keys:
                    url = await appredis.get(fallback_keys[0])
            return url, (room_id, vqn)
        except (ValueError, IndexError):
            return None, None
    return None, None
