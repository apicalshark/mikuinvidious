import asyncio
import functools
import os
import orjson

import httpx
import redis.asyncio as redis
import toml
from bilibili_api import Credential
from bilibili_api.utils.network import request_settings
from quart import Quart, render_template, request
from quart_session import Session
from flask_orjson import OrjsonProvider
import time

from secrets_encryption import decrypt_secret, NACL_AVAILABLE
import nacl.secret


def safe_json_loads(data: str | bytes, default=None):
    """Safely parse JSON with validation."""
    if not data:
        return default
    try:
        # Validate it's a dict or list (not a string/number)
        parsed = orjson.loads(data)
        if not isinstance(parsed, (dict, list)):
            return default
        return parsed
    except (orjson.JSONDecodeError, UnicodeDecodeError, ValueError):
        return default


from bilibili_api.utils.network import get_bili_ticket

def get_common_headers(bili_conf):
    """Get common headers for Bilibili API requests from config."""
    return {
        "User-Agent": bili_conf.get("user_agent", "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google innerVer/8830510 osVer/13 network/2"),
        "Referer": bili_conf.get("referer", "https://www.bilibili.com"),
        "env": bili_conf.get("env", "prod"),
        "app-key": bili_conf.get("app_key", "android64"),
        "x-bili-metadata-ip-region": bili_conf.get("ip_region", "CN"),
        "x-bili-metadata-legal-region": bili_conf.get("legal_region", "CN"),
    }


COMMON_HEADERS = get_common_headers({})


class TicketManager:
    """Manages Bilibili's x-bili-ticket (JWT) for API and CDN requests."""

    _ticket = None
    _expiry = 0
    _lock = asyncio.Lock()

    @classmethod
    def _generate_trace_id(cls):
        """Generates a random x-bili-trace-id (Base64)."""
        return os.urandom(32).hex()  # 256 bits for trace ID

    @classmethod
    def _generate_session_id(cls):
        """Generates a random session_id (32-char hex)."""
        return os.urandom(16).hex()  # 128 bits for session ID

    @classmethod
    async def get_ticket(cls, force_refresh=False):
        async with cls._lock:
            now = int(time.time())
            if not force_refresh:
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

            if force_refresh:
                from bilibili_api.utils.network import refresh_bili_ticket
                refresh_bili_ticket()
                cls._ticket = None
                cls._expiry = 0
                await appredis.delete("miku_bili_ticket")
                await appredis.delete("miku_bili_ticket_expiry")

            # Generate new ticket using upstream bilibili_api
            try:
                # Use upstream implementation. bilibili_api handles its own internal global cache,
                # but we still cache in Redis for cross-process efficiency.
                ticket, expiry_ts = await get_bili_ticket(appcred)
                if ticket:
                    cls._ticket = ticket
                    cls._expiry = int(expiry_ts)
                    real_ttl = cls._expiry - now
                    # Cache in Redis
                    await appredis.setex("miku_bili_ticket", real_ttl, cls._ticket)
                    await appredis.setex("miku_bili_ticket_expiry", real_ttl, str(cls._expiry))
                    return cls._ticket
            except Exception as e:
                print(f"[Ticket] Error fetching ticket from upstream: {e}")

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
                        http2=False,
                        timeout=httpx.Timeout(None, connect=15.0, pool=60.0, read=60.0),
                        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
                        follow_redirects=False,
                    )
        return cls._async_client

    @classmethod
    def get_sync_client(cls) -> httpx.Client:
        if cls._sync_client is None:
            cls._sync_client = httpx.Client(
                proxy=cls.get_proxy(),
                trust_env=False,
                http2=False,
                timeout=10.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                follow_redirects=False,
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
        "debug": os.environ.get("QUART_DEBUG", "false").lower() == "true",
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
    "bili": {
        "user_agent": os.environ.get("BILI_USER_AGENT", "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google innerVer/8830510 osVer/13 network/2"),
        "referer": os.environ.get("BILI_REFERER", "https://www.bilibili.com"),
        "env": os.environ.get("BILI_ENV", "prod"),
        "app_key": os.environ.get("BILI_APP_KEY", "android64"),
        "ip_region": os.environ.get("BILI_IP_REGION", "CN"),
        "legal_region": os.environ.get("BILI_LEGAL_REGION", "CN"),
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
redis_url = appconf["redis"]["redis_url"] or os.environ.get("REDIS_URL")
if redis_url:
    appredis = redis.from_url(redis_url, decode_responses=True)
else:
    appredis = redis.Redis(
        host=appconf["redis"]["host"],
        port=appconf["redis"]["port"],
        username=appconf["redis"]["username"],
        password=appconf["redis"]["password"] or os.environ.get("REDIS_PASSWORD"),
        decode_responses=True,
    )

# Initialize the quart app.
app = Quart("app", template_folder="../templates", static_folder="../static")
app.debug = appconf["server"]["debug"]
app.json_provider_class = OrjsonProvider
app.config.from_mapping(appconf["quart"])
app.config["RESPONSE_TIMEOUT"] = 10800
app.config["BODY_TIMEOUT"] = 10800
# Always generate a random secret key at startup for security
# This invalidates sessions on restart, which is acceptable for this use case
app.secret_key = os.urandom(32).hex()

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
    
    def decrypt_if_encrypted(value: str) -> str:
        """Decrypt value if it appears to be encrypted (base64 encoded)."""
        if not value:
            return value
        # Check if it looks like our encrypted format (base64 with nonce prefix)
        try:
            if NACL_AVAILABLE:
                import base64
                data = base64.b64decode(value)
                if len(data) > nacl.secret.SecretBox.NONCE_SIZE:
                    return decrypt_secret(value)
        except Exception:
            pass
        return value
    
    appcred = Credential(
        sessdata=decrypt_if_encrypted(credstore["sessdata"]),
        bili_jct=decrypt_if_encrypted(credstore["bili_jct"]),
        buvid3=decrypt_if_encrypted(credstore["buvid3"]),
        buvid4=decrypt_if_encrypted(credstore.get("buvid4", "")),
        dedeuserid=decrypt_if_encrypted(credstore["dedeuserid"]),
        ac_time_value=decrypt_if_encrypted(credstore["ac_time_value"]),
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
