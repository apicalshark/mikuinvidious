import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Bilibili analytics / tracking parameters to strip
TRACKING_PARAMS = {
    "spm_id_from",
    "from_spmid",
    "from_source",
    "from",
    "share_source",
    "share_medium",
    "share_plat",
    "share_tag",
    "share_session_id",
    "buvid",
    "bbid",
    "ts",
    "unique_k",
    "vd_source",
    "is_story_h5",
    "theme",
    "broadcast_type",
    "plat_id",
    "mid",
    "up_id",
}

# Regex to find candidate URLs in text
_URL_CANDIDATE_REGEX = re.compile(
    r"(?:https?://[^\s\u4e00-\u9fa5<>\"']+|(?<![/a-zA-Z0-9@.-])(?:[a-zA-Z0-9-]+\.)*b23\.tv/[a-zA-Z0-9]+)",
    re.IGNORECASE,
)

# Trailing punctuation characters commonly found in Chinese or English share text
_TRAILING_PUNCTUATION = "。，！？、）】>\"' )],"


def _is_valid_domain(hostname: str, target_domain: str) -> bool:
    if not hostname:
        return False
    h = hostname.lower()
    return h == target_domain or h.endswith("." + target_domain)


def extract_b23_or_bili_url(text: str) -> str | None:
    """
    Extract a valid b23.tv or bilibili.com URL from plain text or app share messages.
    Prevents SSRF by strictly checking hostnames against allowed domains.
    """
    if not text or not isinstance(text, str):
        return None

    matches = _URL_CANDIDATE_REGEX.findall(text)
    for raw_token in matches:
        token = raw_token.rstrip(_TRAILING_PUNCTUATION)
        if not token.startswith("http://") and not token.startswith("https://"):
            token = "https://" + token

        try:
            parsed = urlparse(token)
            hostname = parsed.hostname
            if not hostname:
                continue

            if _is_valid_domain(hostname, "b23.tv"):
                # Normalize b23 shortlink to https://b23.tv/<code-or-id>
                path_parts = [p for p in parsed.path.strip("/").split("/") if p]
                if path_parts:
                    return f"https://b23.tv/{path_parts[0]}"

            elif _is_valid_domain(hostname, "bilibili.com"):
                # Clean full Bilibili URL candidate
                return token
        except Exception:
            continue

    return None


def clean_bilibili_url(target_url: str) -> dict:
    """
    De-track a Bilibili target URL by stripping tracking parameters,
    and classify the resource into type, ID, and local MikuInvidious path.
    """
    if not target_url:
        return {"clean_url": "", "local_path": "", "type": "unknown", "id": ""}

    parsed = urlparse(target_url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path
    query = parse_qs(parsed.query, keep_blank_values=False)

    # Filter out tracking query parameters
    filtered_query = {}
    for k, v in query.items():
        k_lower = k.lower()
        if (
            k_lower in TRACKING_PARAMS
            or k_lower.startswith("utm_")
            or k_lower.startswith("spm_")
            or k_lower.startswith("share_")
            or k_lower.startswith("track_")
        ):
            continue
        filtered_query[k] = v

    # Preserve functional params (p, t, etc.)
    clean_query_str = urlencode(filtered_query, doseq=True)
    p_val = filtered_query.get("p", [None])[0]

    res_type = "unknown"
    res_id = ""
    local_path = ""
    canonical_clean_url = ""

    # 1. Video (/video/BV... or /video/av...)
    m_video = re.search(r"/video/((?:BV[a-zA-Z0-9]+)|(?:av[0-9]+))", path, re.IGNORECASE)
    if m_video:
        res_type = "video"
        res_id = m_video.group(1)
        canonical_clean_url = f"https://www.bilibili.com/video/{res_id}"
        if p_val and p_val.isdigit() and int(p_val) > 1:
            local_path = f"/video/{res_id}:{p_val}"
        else:
            local_path = f"/video/{res_id}"

    # 2. Bangumi ep (/bangumi/play/ep...)
    elif m_ep := re.search(r"/bangumi/play/ep([0-9]+)", path, re.IGNORECASE):
        res_type = "bangumi_ep"
        res_id = f"ep{m_ep.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/bangumi/play/{res_id}"
        local_path = f"/bangumi/play/{res_id}"

    # 3. Bangumi ss (/bangumi/play/ss...)
    elif m_ss := re.search(r"/bangumi/play/ss([0-9]+)", path, re.IGNORECASE):
        res_type = "bangumi_ss"
        res_id = f"ss{m_ss.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/bangumi/play/{res_id}"
        local_path = f"/bangumi/play/{res_id}"

    # 4. Read cv (/read/cv... or /read/mobile/...)
    elif m_cv := re.search(r"/read/(?:mobile/)?(?:cv)?([0-9]+)", path, re.IGNORECASE):
        res_type = "read"
        res_id = f"cv{m_cv.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/read/{res_id}"
        local_path = f"/read/{res_id}"

    # 5. Opus (/opus/...)
    elif m_opus := re.search(r"/opus/([0-9]+)", path, re.IGNORECASE):
        res_type = "opus"
        res_id = f"opus{m_opus.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/opus/{m_opus.group(1)}"
        local_path = f"/read/{res_id}"

    # 6. Live (live.bilibili.com/<id> or /live/<id>)
    elif (hostname == "live.bilibili.com" and (m_live := re.search(r"^/([0-9]+)", path))) or (
        m_live := re.search(r"/live/([0-9]+)", path, re.IGNORECASE)
    ):
        res_type = "live"
        res_id = m_live.group(1)
        canonical_clean_url = f"https://live.bilibili.com/{res_id}"
        local_path = f"/live/{res_id}"

    # 7. Space (space.bilibili.com/<mid> or /space/<mid>)
    elif (hostname == "space.bilibili.com" and (m_space := re.search(r"^/([0-9]+)", path))) or (
        m_space := re.search(r"/space/([0-9]+)", path, re.IGNORECASE)
    ):
        res_type = "space"
        res_id = m_space.group(1)
        canonical_clean_url = f"https://space.bilibili.com/{res_id}"
        local_path = f"/space/{res_id}"

    # 8. Audio (au and am)
    elif m_au := re.search(r"/audio/au([0-9]+)", path, re.IGNORECASE):
        res_type = "audio"
        res_id = f"au{m_au.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/audio/{res_id}"
        local_path = f"/audio/{res_id}"

    elif m_am := re.search(r"/audio/am([0-9]+)", path, re.IGNORECASE):
        res_type = "audio_list"
        res_id = f"am{m_am.group(1)}"
        canonical_clean_url = f"https://www.bilibili.com/audio/{res_id}"
        local_path = f"/audio_list/{res_id}"

    else:
        canonical_clean_url = urlunparse((
            parsed.scheme or "https",
            parsed.netloc or "www.bilibili.com",
            path,
            "",
            "",
            "",
        ))
        local_path = path

    # Append clean query string to canonical clean URL if present
    if clean_query_str:
        final_clean_url = f"{canonical_clean_url}?{clean_query_str}"
    else:
        final_clean_url = canonical_clean_url

    return {
        "clean_url": final_clean_url,
        "local_path": local_path,
        "type": res_type,
        "id": res_id,
    }


async def resolve_b23_target(input_text: str, client=None) -> dict:
    """
    Safely resolve a b23.tv shortlink or Bilibili URL into clean and local formats.
    Returns:
        {
            "status": "ok" | "error",
            "original": str,
            "target_url": str,
            "clean_url": str,
            "local_path": str,
            "type": str,
            "id": str,
            "error": Optional[str],
        }
    """
    def error_ret(err: str) -> dict:
        return {
            "status": "error",
            "original": input_text or "",
            "target_url": "",
            "clean_url": "",
            "local_path": "",
            "type": "",
            "id": "",
            "error": err,
        }

    extracted = extract_b23_or_bili_url(input_text)
    if not extracted:
        return error_ret("No valid b23.tv or Bilibili URL found in input")

    parsed = urlparse(extracted)
    hostname = (parsed.hostname or "").lower()

    # Direct bilibili.com URL -> Clean directly without redirect hop
    if _is_valid_domain(hostname, "bilibili.com"):
        cleaned = clean_bilibili_url(extracted)
        return {
            "status": "ok",
            "original": input_text,
            "target_url": extracted,
            "clean_url": cleaned["clean_url"],
            "local_path": cleaned["local_path"],
            "type": cleaned["type"],
            "id": cleaned["id"],
            "error": None,
        }

    # Strict SSRF check for b23.tv
    if not _is_valid_domain(hostname, "b23.tv"):
        return error_ret(f"Invalid domain: {hostname}")

    if client is None:
        import shared
        client = await shared.Network.get_async_client()

    try:
        resp = await client.get(extracted, follow_redirects=False, timeout=10.0)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return error_ret(f"Failed to resolve shortlink: HTTP {resp.status_code}")

        location = resp.headers.get("Location")
        if not location:
            return error_ret("Redirect response missing Location header")

        loc_parsed = urlparse(location)
        loc_host = (loc_parsed.hostname or "").lower()

        # Strict SSRF verification on redirect destination
        if loc_host:
            if not (_is_valid_domain(loc_host, "bilibili.com") or _is_valid_domain(loc_host, "b23.tv")):
                return error_ret(f"Invalid or unsafe redirect destination: {loc_host}")
        elif not location.startswith("/"):
            return error_ret(f"Invalid redirect destination: {location}")

        if location.startswith("/"):
            location = f"https://www.bilibili.com{location}"

        cleaned = clean_bilibili_url(location)
        return {
            "status": "ok",
            "original": input_text,
            "target_url": location,
            "clean_url": cleaned["clean_url"],
            "local_path": cleaned["local_path"],
            "type": cleaned["type"],
            "id": cleaned["id"],
            "error": None,
        }
    except Exception as e:
        return error_ret(str(e))
