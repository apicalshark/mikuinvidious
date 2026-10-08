import asyncio
import functools
import os
import time

import httpx
import nacl.secret
import orjson
import redis.asyncio as redis
import toml
from api import Credential
from api.client import get_bili_ticket, request_settings
from flask_orjson import OrjsonProvider
from quart import Quart, render_template, request
from quart_session import Session
from secrets_encryption import NACL_AVAILABLE, decrypt_secret


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


from api.client import get_bili_ticket


def get_common_headers(bili_conf):
    """Get common headers for Bilibili API requests from config."""
    return {
        "User-Agent": bili_conf.get(
            "user_agent",
            "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google innerVer/8830510 osVer/13 network/2",
        ),
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
    # Future shared by concurrent callers while a refresh is running, so a
    # ticket miss stampedes one upstream fetch instead of N (singleflight).
    _inflight = None

    @classmethod
    def _generate_trace_id(cls):
        """Generates a random x-bili-trace-id (Base64)."""
        return os.urandom(32).hex()  # 256 bits for trace ID

    @classmethod
    def _generate_session_id(cls):
        """Generates a random session_id (32-char hex)."""
        return os.urandom(16).hex()  # 128 bits for session ID

    @classmethod
    async def _fetch_new_ticket(cls):
        """Fetch + Redis-cache a fresh ticket. Runs outside the lock.

        Returns ``(ticket, expiry_ts)``; ``(None, 0)`` on any failure.
        """
        now = int(time.time())
        try:
            # Use upstream implementation. bilibili_api handles its own internal global cache,
            # but we still cache in Redis for cross-process efficiency.
            ticket, expiry_ts = await get_bili_ticket(appcred)
        except Exception as e:
            print(f"[Ticket] Error fetching ticket from upstream: {e}")
            return None, 0
        if not ticket:
            return None, 0
        try:
            real_ttl = int(expiry_ts) - now
            if real_ttl > 0:
                pipe = appredis.pipeline(transaction=False)
                pipe.setex("miku_bili_ticket", real_ttl, ticket)
                pipe.setex("miku_bili_ticket_expiry", real_ttl, str(expiry_ts))
                await pipe.execute()
        except Exception as e:
            print(f"[Ticket] Error caching ticket in Redis: {e}")
        return ticket, expiry_ts

    @classmethod
    async def get_ticket(cls, force_refresh=False):
        async with cls._lock:
            now = int(time.time())
            if not force_refresh:
                # Check local cache
                if cls._ticket and now < cls._expiry - 60:
                    return cls._ticket

                # Check Redis cache (a miss/degraded Redis falls through to fetch)
                try:
                    cached_ticket, cached_expiry = await appredis.mget("miku_bili_ticket", "miku_bili_ticket_expiry")
                except Exception:
                    cached_ticket, cached_expiry = None, None
                if cached_ticket and cached_expiry and now < int(cached_expiry) - 60:
                    cls._ticket = cached_ticket
                    cls._expiry = int(cached_expiry)
                    return cls._ticket
            else:
                from api.client import refresh_bili_ticket

                refresh_bili_ticket()
                cls._ticket = None
                cls._expiry = 0
                try:
                    await appredis.delete("miku_bili_ticket")
                    await appredis.delete("miku_bili_ticket_expiry")
                except Exception:
                    pass

            if cls._inflight is None:
                cls._inflight = asyncio.get_running_loop().create_future()
                fetch = True
            else:
                fetch = False
                fut = cls._inflight
        if not fetch:
            return await fut
        ticket, expiry_ts = await cls._fetch_new_ticket()
        async with cls._lock:
            fut = cls._inflight
            cls._inflight = None
            if ticket:
                cls._ticket = ticket
                cls._expiry = int(expiry_ts)
            if fut is not None and not fut.done():
                fut.set_result(ticket)
        return ticket


class Network:
    _async_client = None
    _sync_client = None
    _async_lock = asyncio.Lock()

    @staticmethod
    def get_proxy():
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
                        timeout=httpx.Timeout(None, connect=15.0, pool=30.0, read=30.0),
                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
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


def _int_env(name, default):
    """Parse an int env var, falling back to default on missing/invalid values."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


appconf = {
    "site": {
        "site_name": os.environ.get("SITE_NAME", "MikuInvidious"),
        "site_url": os.environ.get("SITE_URL", "https://example.org"),
        "site_modified_source_code_url": os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "")
        if os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "").lower() not in ["false", ""]
        else False,
        "site_allow_download": os.environ.get("SITE_ALLOW_DOWNLOAD", "true").lower() == "true",
        "max_download_size_mb": int(os.environ.get("MAX_DOWNLOAD_SIZE_MB", 1024)),
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
        "monitor_fd": os.environ.get("MONITOR_FD", "false").lower() == "true",
        # Granian per-request access log. Off by default: at one line per
        # segment/image/status-poll it buries the real app logs.
        # config.toml [server] access_log = true / SERVER_ACCESS_LOG=true to re-enable.
        "access_log": os.environ.get("SERVER_ACCESS_LOG", "false").lower() == "true",
    },
    "display": {
        "default_theme": "modern",
        "default_locale": os.environ.get("DEFAULT_LOCALE", "zh-CN"),
        # Unset/empty means auto-discover from locales/*/LC_MESSAGES.
        "supported_locales": os.environ.get("SUPPORTED_LOCALES"),
    },
    "live": {
        # Server-side live format policy: FLV first, HLS master as fallback.
        # Set LIVE_PREFER_HLS=true to reverse it (HLS first, FLV fallback).
        "prefer_hls": os.environ.get("LIVE_PREFER_HLS", "false").lower() == "true",
    },
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
        "proxy_url": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"),
    },
    "render": {
        "use_pandoc": os.environ.get("USE_PANDOC", "false").lower() == "true",
        "article_allowed_formats": os.environ.get("ARTICLE_ALLOWED_FORMATS", "markdown,plain,html").split(","),
    },
    "bili": {
        "user_agent": os.environ.get(
            "BILI_USER_AGENT",
            "Mozilla/5.0 BiliDroid/8.83.0 (bbcallen@gmail.com) 8.83.0 os/android model/MI 9 mobi_app/android build/8830500 channel/html5_search_google innerVer/8830510 osVer/13 network/2",
        ),
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
    "rate_limit": {
        "enabled": os.environ.get("RATE_LIMIT_ENABLED", "false").lower() == "true",
        "trusted_proxies": os.environ.get("TRUSTED_PROXIES", ""),
    },
    "cache": {
        # TTLs in minutes for page-data caches. Each route stores its upstream
        # payload under its own Redis key; /space/<mid> and /space/<mid>/json
        # additionally share one key (space:data:<mid>) so one Bilibili fetch
        # serves both. Overridable via config.toml [cache] or the env vars.
        # Set to 0 to disable caching for that route.
        "space_minutes": _int_env("SPACE_CACHE_MINUTES", 5),
        "space_json_minutes": _int_env("SPACE_JSON_CACHE_MINUTES", 5),
        "video_minutes": _int_env("VIDEO_CACHE_MINUTES", 15),
        "bangumi_minutes": _int_env("BANGUMI_CACHE_MINUTES", 60),
        "author_minutes": _int_env("AUTHOR_CACHE_MINUTES", 30),
        "article_minutes": _int_env("ARTICLE_CACHE_MINUTES", 30),
        "audio_minutes": _int_env("AUDIO_CACHE_MINUTES", 30),
        "home_minutes": _int_env("HOME_CACHE_MINUTES", 30),
    },
}

if os.path.exists("config.toml"):
    deep_update(appconf, toml.load("config.toml"))
elif os.path.exists("../config.toml"):
    deep_update(appconf, toml.load("../config.toml"))

# Install the locale allowlist now that config is merged, so locale resolution,
# /set_lang validation and the template language menus all read the same list.
from i18n import set_supported_locales  # noqa: E402

set_supported_locales(appconf["display"].get("supported_locales"))

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


def cache_minutes(key, default=5):
    """Read a [cache] TTL (in minutes); invalid values fall back to default."""
    try:
        return int(appconf.get("cache", {}).get(key, default))
    except (TypeError, ValueError):
        return default


async def cache_get(key, max_age_seconds):
    """Return the cached payload dict if fresh, else None.

    Entries carry a ``fetched_at`` stamp; readers older than their own
    max-age treat the entry as expired. Anything unreadable (Redis down,
    corrupt JSON, legacy entries without a stamp) is a silent miss so the
    caller falls back to live data instead of 500ing.
    """
    try:
        raw = await appredis.get(key)
    except Exception:
        return None
    data = safe_json_loads(raw, default=None)
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    if age < 0 or age > max_age_seconds:
        return None
    return data


async def cache_set(key, payload, ttl_seconds):
    """Store a payload dict with a fetched_at stamp; failures are silent."""
    if ttl_seconds <= 0 or not isinstance(payload, dict):
        return
    try:
        payload = dict(payload)
        payload["fetched_at"] = time.time()
        raw = orjson.dumps(payload)
        await appredis.set(
            key,
            raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw),
            ex=ttl_seconds,
        )
    except Exception:
        pass


# Maintain a simple Redis-based cache for views


class SimpleCache:
    # Per-key in-flight renders: [future, waiter_count]. Concurrent cache
    # misses coalesce behind a single upstream render instead of stampeding
    # (thundering herd). The future is settled only while joiners remain, so
    # unobserved failures never log "exception was never retrieved".
    _inflight: dict = {}

    @staticmethod
    def _settle(cache_key, fut, ok, payload):
        """Settle joiners of *cache_key* iff *fut* is still the live entry."""
        entry = SimpleCache._inflight.get(cache_key)
        if entry is not None and entry[0] is fut:
            del SimpleCache._inflight[cache_key]
            if entry[1] and not fut.done():
                if ok:
                    fut.set_result(payload)
                else:
                    fut.set_exception(payload)

    @staticmethod
    def _join(cache_key):
        """Register on the in-flight render for *cache_key*; returns (future, is_owner).

        No await runs between lookup and registration, so this is atomic on
        one event loop and exactly one task becomes the owner.
        """
        entry = SimpleCache._inflight.get(cache_key)
        if entry is None:
            entry = [asyncio.get_running_loop().create_future(), 0]
            SimpleCache._inflight[cache_key] = entry
            return entry[0], True
        entry[1] += 1
        return entry[0], False

    def cached(self, timeout=300, key_prefix="view/%s"):
        def decorator(f):
            @functools.wraps(f)
            async def decorated_function(*args, **kwargs):
                """Serve cached HTML or render once per key across concurrent misses."""
                # Avoid caching during POST or when arguments exist in some cases
                # But for simplicity, we use the full path as the key
                cache_key = key_prefix % request.full_path

                # Check if we have a cached version
                cached_val = await appredis.get(cache_key)
                if cached_val:
                    return cached_val

                # Coalesce concurrent misses on the same key.
                fut, owner = SimpleCache._join(cache_key)

                if not owner:
                    # Shield: a cancelled joiner must not cancel the shared
                    # future for the remaining joiners.
                    try:
                        return await asyncio.shield(fut)
                    except asyncio.CancelledError:
                        cur = SimpleCache._inflight.get(cache_key)
                        if cur is not None and cur[0] is fut and cur[1] > 0:
                            cur[1] -= 1
                        raise

                try:
                    # Otherwise, call the function and cache the result
                    response = await f(*args, **kwargs)

                    # Only cache if it's a successful string response (rendered template)
                    if isinstance(response, str):
                        await appredis.setex(cache_key, timeout, response)

                    SimpleCache._settle(cache_key, fut, True, response)
                    return response
                except asyncio.CancelledError:
                    # A cancelled owner must not strand joiners: hand them a
                    # retryable error (their next request re-renders) and let
                    # the cancellation propagate.
                    SimpleCache._settle(cache_key, fut, False, RuntimeError("cached render cancelled"))
                    raise
                except Exception as e:
                    SimpleCache._settle(cache_key, fut, False, e)
                    raise

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
                    try:
                        return decrypt_secret(value)
                    except Exception as e:
                        print(f"[Security] Failed to decrypt credential: {e}. Using raw value.")
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


def detect_locale():
    """Resolve UI locale: ?lang= > lang cookie > Accept-Language > default."""
    from i18n import detect_locale as _detect

    return _detect(
        request.args.get("lang"),
        request.cookies.get("lang"),
        request.headers.get("Accept-Language"),
        appconf["display"].get("default_locale", "en"),
    )


async def render_template_with_theme(fp, **kwargs):
    """Render a template with theming and locale support."""
    from i18n import get_json_catalog, gettext_msg, ngettext_msg

    t = detect_theme()
    locale = detect_locale()
    _ = lambda s: gettext_msg(locale, s)  # noqa: E731

    dark_theme = request.cookies.get("dark-theme") == "1"

    # Backend UI-string translation at the single render choke point so all
    # error pages (and `message` notices) translate without touching every
    # view call site (call sites stay English canonical msgids). `title` is
    # intentionally excluded (often dynamic, e.g. video titles). gettext
    # returns unknown msgids unchanged, so dynamic strings with variable
    # suffixes (e.g. f"Backend error: {e.msg}") pass through safely.
    for key in ("status", "desc", "suggest", "message"):
        val = kwargs.get(key)
        if isinstance(val, str):
            kwargs[key] = gettext_msg(locale, val)

    return await render_template(
        f"themes/{t}/{fp}",
        dark_mode=dark_theme,
        proxy_status=appconf["proxy"],
        locale=locale,
        _=_,
        gettext=lambda s: gettext_msg(locale, s),
        ngettext=lambda s, p, n: ngettext_msg(locale, s, p, n),
        i18n_catalog=get_json_catalog(locale),
        **appconf["site"],
        **kwargs,
    )


# --- GLOBAL PROXY CONFIGURATION FOR BILIBILI_API ---
# Proxying is always on; proxy_url selects the WARP tunnel when set,
# otherwise traffic goes direct.
proxy_url = Network.get_proxy()
if proxy_url:
    print(f"[Init] Setting global proxy for bilibili_api: {proxy_url}")
    request_settings.set_proxy(proxy_url)
else:
    print("[Init] No proxy URL found in config.toml or env vars, using direct connection.")
