import asyncio
import functools
import os

import httpx
import redis.asyncio as redis
import toml
from bilibili_api import Credential
from bilibili_api.utils.network import request_settings
from quart import Quart, render_template, request
from quart_session import Session
from flask_orjson import OrjsonProvider


import hmac
import hashlib
import time

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


class TicketManager:
    """Manages Bilibili's x-bili-ticket (JWT) for API and CDN requests."""

    _ticket = None
    _expiry = 0
    _lock = asyncio.Lock()

    @classmethod
    def _generate_trace_id(cls):
        """Generates a random x-bili-trace-id (Base64)."""
        return os.urandom(16).hex()  # Simple hex representation for now

    @classmethod
    def _generate_session_id(cls):
        """Generates a random session_id (8-char hex)."""
        return os.urandom(4).hex()

    @classmethod
    async def get_ticket(cls):
        async with cls._lock:
            now = int(time.time())
            # Check local cache
            if cls._ticket and now < cls._expiry - 60:
                return cls._ticket

            # Check Redis cache
            cached_ticket = await appredis.get("miku_bili_ticket")
            cached_expiry = await appredis.get("miku_bili_ticket_expiry")
            if cached_ticket and cached_expiry and now < int(cached_expiry) - 60:
                cls._ticket = cached_ticket
                cls._expiry = int(cached_expiry)
                return cls._ticket

            # Generate new ticket
            try:
                ticket_data = await cls._fetch_new_ticket()
                if ticket_data:
                    cls._ticket = ticket_data["ticket"]
                    # JWT real expiry is ~8 hours, while API TTL is 3 days.
                    # We cap the expiry at 7 hours to be safe.
                    real_ttl = min(int(ticket_data["ttl"]), 7 * 3600)
                    cls._expiry = now + real_ttl
                    # Cache in Redis
                    await appredis.setex("miku_bili_ticket", real_ttl, cls._ticket)
                    await appredis.setex("miku_bili_ticket_expiry", real_ttl, str(cls._expiry))
                    return cls._ticket
            except Exception as e:
                print(f"[Ticket] Error fetching ticket: {e}")

            return None

    @classmethod
    async def _fetch_new_ticket(cls):
        """Calls GenWebTicket API to get a new JWT ticket."""
        # Use Android key (ec01) for BiliDroid UA compatibility
        key_id = "ec01"
        key = b"Ezlc3tgtl"
        ts = int(time.time())

        # HMAC-SHA256(key, "ts" + ts)
        hexsign = hmac.new(key, f"ts{ts}".encode(), hashlib.sha256).hexdigest()

        url = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
        params = {
            "key_id": key_id,
            "hexsign": hexsign,
            "context[ts]": ts,
            "csrf": appconf["credential"].get("bili_jct", ""),
        }

        # Need to include buvid3 in cookies if available
        cookies = {}
        if appconf["credential"].get("buvid3"):
            cookies["buvid3"] = appconf["credential"]["buvid3"]

        client = await Network.get_async_client()
        try:
            # Use the specific BiliDroid headers for this call
            headers = COMMON_HEADERS.copy()
            # buvid is often required as a header too
            if appconf["credential"].get("buvid3"):
                headers["buvid"] = appconf["credential"]["buvid3"]
            
            resp = await client.post(url, params=params, cookies=cookies, headers=headers, timeout=10.0)
            data = resp.json()
            if data.get("code") == 0:
                print(f"[Ticket] Successfully fetched new ticket. TTL: {data['data']['ttl']}s")
                return data["data"]
            else:
                print(f"[Ticket] API returned error {data.get('code')}: {data.get('message')}")
        except Exception as e:
            print(f"[Ticket] Request failed: {e}")

        return None


class Network:
    _async_client = None
    _sync_client = None
    _async_lock = asyncio.Lock()

    @staticmethod
    def get_proxy():
        if not appconf["proxy"]["use_proxy"]:
            return None
        return appconf["proxy"]["proxy_url"] or None

    @classmethod
    async def get_async_client(cls) -> httpx.AsyncClient:
        if cls._async_client is None or cls._async_client.is_closed:
            async with cls._async_lock:
                if cls._async_client is None or cls._async_client.is_closed:
                    cls._async_client = httpx.AsyncClient(
                        proxy=cls.get_proxy(),
                        trust_env=False,
                        http2=True,
                        timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200),
                        follow_redirects=True,
                    )
        return cls._async_client

    @classmethod
    def get_sync_client(cls) -> httpx.Client:
        if cls._sync_client is None:
            cls._sync_client = httpx.Client(
                proxy=cls.get_proxy(),
                trust_env=False,
                timeout=10.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return cls._sync_client


# Maintain backward compatibility
def get_global_httpx_client(async_client=True):
    return Network.get_async_client() if async_client else Network.get_sync_client()


# Semaphore for image proxying
image_limiter = asyncio.Semaphore(50)


def deep_update(base_dict, update_dict):
    for key, value in update_dict.items():
        if isinstance(value, dict) and key in base_dict and isinstance(base_dict[key], dict):
            deep_update(base_dict[key], value)
        else:
            base_dict[key] = value


appconf = {
    "site": {
        "site_name": os.environ.get("SITE_NAME", "MikuInvidious"),
        "site_url": os.environ.get("SITE_URL", "https://example.org"),
        "site_modified_source_code_url": os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "")
        if os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "").lower() not in ["false", ""]
        else False,
        "site_allow_download": os.environ.get("SITE_ALLOW_DOWNLOAD", "true").lower() == "true",
        "site_show_unsafe_error_response": os.environ.get("SITE_SHOW_UNSAFE_ERROR_RESPONSE", "false").lower() == "true",
        "nyaa_bangumi": os.environ.get("NYAA_BANGUMI", "false").lower() == "true",
        "robots_policy": os.environ.get("ROBOTS_POLICY", "strict"),
    },
    "quart": {},
    "server": {
        "host": os.environ.get("SERVER_HOST", "0.0.0.0"),
        "port": int(os.environ.get("SERVER_PORT", 8888)),
        "secret_key": os.environ.get("QUART_SECRET_KEY"),
    },
    "display": {"default_theme": "modern"},
    "credential": {
        "use_cred": os.environ.get("USE_CRED", "false").lower() == "true",
        "sessdata": os.environ.get("SESSDATA"),
        "bili_jct": os.environ.get("BILI_JCT"),
        "buvid3": os.environ.get("BUVID3"),
        "buvid4": os.environ.get("BUVID4"),
        "dedeuserid": os.environ.get("DEDEUSERID"),
        "ac_time_value": os.environ.get("AC_TIME_VALUE"),
    },
    "proxy": {
        "use_proxy": os.environ.get("NO_PROXY", "false").lower() not in ["true", "1"],
        "proxy_url": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
    },
    "render": {
        "use_pandoc": os.environ.get("USE_PANDOC", "false").lower() == "true",
        "article_allowed_formats": os.environ.get("ARTICLE_ALLOWED_FORMATS", "markdown,plain,html").split(","),
    },
    "redis": {
        "host": os.environ.get("REDIS_HOST", "localhost"),
        "port": int(os.environ.get("REDIS_PORT", 6379)),
        "username": os.environ.get("REDIS_USERNAME"),
        "password": os.environ.get("REDIS_PASSWORD"),
        "redis_url": os.environ.get("REDIS_URL"),
    },
}

if os.path.exists("config.toml"):
    deep_update(appconf, toml.load("config.toml"))
elif os.path.exists("../config.toml"):
    deep_update(appconf, toml.load("../config.toml"))

# Connect to our nice redis database.
if appconf["redis"]["redis_url"]:
    appredis = redis.from_url(appconf["redis"]["redis_url"], decode_responses=True)
else:
    appredis = redis.Redis(
        host=appconf["redis"]["host"],
        port=appconf["redis"]["port"],
        username=appconf["redis"]["username"],
        password=appconf["redis"]["password"],
        decode_responses=True,
    )

# Initialize the quart app.
app = Quart("app", template_folder="../templates", static_folder="../static")
app.json_provider_class = OrjsonProvider
app.config.from_mapping(appconf["quart"])
app.config["RESPONSE_TIMEOUT"] = 10800
app.config["BODY_TIMEOUT"] = 10800
app.secret_key = appconf["server"]["secret_key"] or os.urandom(24).hex()

# Configure sessions
app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_REDIS"] = appredis
Session(app)


async def close_global_client():
    """Cleanup global resources. Called on app shutdown."""
    if Network._async_client and not Network._async_client.is_closed:
        await Network._async_client.aclose()
        print("[Shutdown] Global async client closed.")


# Maintain a simple Redis-based cache for views


class SimpleCache:
    def cached(self, timeout=300, key_prefix="view/%s"):
        def decorator(f):
            @functools.wraps(f)
            async def decorated_function(*args, **kwargs):
                # Avoid caching during POST or when arguments exist in some cases
                # But for simplicity, we use the full path as the key
                cache_key = key_prefix % request.full_path

                # Check if we have a cached version
                cached_val = await appredis.get(cache_key)
                if cached_val:
                    return cached_val

                # Otherwise, call the function and cache the result
                response = await f(*args, **kwargs)

                # Only cache if it's a successful string response (rendered template)
                if isinstance(response, str):
                    await appredis.setex(cache_key, timeout, response)

                return response

            return decorated_function

        return decorator


appcache = SimpleCache()

# Initialize credentials for bilibili API.
appcred = None
if appconf["credential"]["use_cred"]:
    credstore = appconf["credential"]
    appcred = Credential(
        sessdata=credstore["sessdata"],
        bili_jct=credstore["bili_jct"],
        buvid3=credstore["buvid3"],
        buvid4=credstore.get("buvid4"),
        dedeuserid=credstore["dedeuserid"],
        ac_time_value=credstore["ac_time_value"],
    )

##########################################
# Util functions
##########################################


def detect_theme():
    """Determine the theme of the users' request."""
    theme = request.args.get("theme") or request.cookies.get("theme") or appconf["display"]["default_theme"]
    return theme


async def render_template_with_theme(fp, **kwargs):
    """Render a template with theming support."""
    t = detect_theme()

    dark_theme = request.cookies.get("dark-theme") == "1"

    return await render_template(
        f"themes/{t}/{fp}",
        dark_mode=dark_theme,
        proxy_status=appconf["proxy"],
        **appconf["site"],
        **kwargs,
    )


# --- GLOBAL PROXY CONFIGURATION FOR BILIBILI_API ---
if appconf["proxy"]["use_proxy"]:
    proxy_url = Network.get_proxy()
    if proxy_url:
        print(f"[Init] Setting global proxy for bilibili_api: {proxy_url}")
        request_settings.set_proxy(proxy_url)
    else:
        print(
            "[Init] Proxy enabled but no proxy URL found in config.toml or env vars! Falling back to direct connection."
        )
