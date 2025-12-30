import asyncio
import functools
import os

import httpx
import toml
from redis.asyncio import Redis
from bilibili_api import Credential
from bilibili_api.utils.network import request_settings
from quart import Quart, render_template, request
from quart_session import Session


class Network:
    _async_client = None
    _sync_client = None
    _async_lock = asyncio.Lock()

    @staticmethod
    def get_proxy():
        if not appconf["proxy"]["use_proxy"]:
            return None
        return os.environ.get("SOCKS5_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")

    @classmethod
    async def get_async_client(cls) -> httpx.AsyncClient:
        if cls._async_client is None or cls._async_client.is_closed:
            async with cls._async_lock:
                if cls._async_client is None or cls._async_client.is_closed:
                    cls._async_client = httpx.AsyncClient(
                        proxy=cls.get_proxy(),
                        trust_env=False,
                        http2=True,
                        timeout=httpx.Timeout(None, connect=10.0),
                        limits=httpx.Limits(
                            max_connections=1000,
                            max_keepalive_connections=100,
                            keepalive_expiry=30.0,
                        ),
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
        "site_modified_source_code_url": os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "false").lower() == "true",
        "site_allow_download": os.environ.get("SITE_ALLOW_DOWNLOAD", "true").lower() == "true",
        "site_show_unsafe_error_response": os.environ.get("SITE_SHOW_UNSAFE_ERROR_RESPONSE", "false").lower() == "true",
        "robots_policy": os.environ.get("ROBOTS_POLICY", "strict"),
    },
    "quart": {},
    "server": {
        "host": os.environ.get("SERVER_HOST", "0.0.0.0"),
        "port": int(os.environ.get("SERVER_PORT", 8888)),
    },
    "display": {"default_theme": "modern"},
    "credential": {
        "use_cred": os.environ.get("USE_CRED", "false").lower() == "true",
        "sessdata": os.environ.get("SESSDATA"),
        "bili_jct": os.environ.get("BILI_JCT"),
        "buvid3": os.environ.get("BUVID3"),
        "dedeuserid": os.environ.get("DEDEUSERID"),
        "ac_time_value": os.environ.get("AC_TIME_VALUE"),
    },
    "proxy": {
        "use_proxy": os.environ.get("NO_PROXY", "false").lower() not in ["true", "1"],
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
    },
}

if os.path.exists("config.toml"):
    deep_update(appconf, toml.load("config.toml"))
elif os.path.exists("../config.toml"):
    deep_update(appconf, toml.load("../config.toml"))

# Connect to our nice redis database.
if os.environ.get("REDIS_URL"):
    appredis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
else:
    appredis = Redis(
        host=appconf["redis"]["host"],
        port=appconf["redis"]["port"],
        username=appconf["redis"]["username"],
        password=appconf["redis"]["password"],
        decode_responses=True,
    )

# Initialize the quart app.
app = Quart("app", template_folder="../templates", static_folder="../static")
app.config.from_mapping(appconf["quart"])
app.config["RESPONSE_TIMEOUT"] = 86400
app.config["BODY_TIMEOUT"] = 86400
app.config["QUART_RESPONSE_STREAM"] = True
app.secret_key = os.environ.get("QUART_SECRET_KEY", os.urandom(24).hex())

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
        dedeuserid=credstore["dedeuserid"],
        ac_time_value=credstore["ac_time_value"],
    )


def get_current_cred():
    """Retrieve credentials dynamically: User Session > Global Config."""
    from quart import session

    # 1. Check if user has personal login in session
    user_creds = session.get("bili_creds")
    if user_creds:
        return Credential(**user_creds)

    # 2. Fallback to global server-wide creds
    return appcred


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
proxy_url = Network.get_proxy()
if proxy_url:
    print(f"[Init] Setting global proxy for bilibili_api: {proxy_url}")
    request_settings.set_proxy(proxy_url)
else:
    print("[Init] No proxy configured. Using direct connection.")
