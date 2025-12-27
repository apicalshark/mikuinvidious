import os
import redis
import toml
import httpx
import asyncio
import time

from quart import request, render_template, Quart
from quart_session import Session

from bilibili_api import Credential
from refresher import renew_cookies

class Network:
    _async_client = None
    _sync_client = None
    
    @staticmethod
    def get_proxy():
        return os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy') if appconf['proxy']['use_proxy'] else None

    @classmethod
    def get_async_client(cls) -> httpx.AsyncClient:
        if cls._async_client is None or cls._async_client.is_closed:
            cls._async_client = httpx.AsyncClient(
                proxy=cls.get_proxy(),
                trust_env=False,
                timeout=httpx.Timeout(10.0, read=None), # No timeout for reading streams by default
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
            )
        return cls._async_client

    @classmethod
    def get_sync_client(cls) -> httpx.Client:
        if cls._sync_client is None:
            cls._sync_client = httpx.Client(
                proxy=cls.get_proxy(),
                trust_env=False,
                timeout=10.0
            )
        return cls._sync_client

# Maintain backward compatibility
get_global_httpx_client = lambda async_client=True: Network.get_async_client() if async_client else Network.get_sync_client()

# Semaphore for image proxying
image_limiter = asyncio.Semaphore(10)

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
    "display": {
        "default_theme": "modern"
    },
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
    }
}

if os.path.exists('config.toml'):
    deep_update(appconf, toml.load('config.toml'))
elif os.path.exists('../config.toml'):
    deep_update(appconf, toml.load('../config.toml'))

# Connect to our nice redis database.
if os.environ.get("REDIS_URL"):
    appredis = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
else:
    appredis = redis.Redis(
        host=appconf['redis']['host'],
        port=appconf['redis']['port'],
        username=appconf['redis']['username'],
        password=appconf['redis']['password'],
        decode_responses=True
    )

# Initialize the quart app.
app = Quart('app', template_folder='../templates', static_folder='../static')
app.config.from_mapping(appconf['quart'])
app.secret_key = os.environ.get("QUART_SECRET_KEY", os.urandom(24).hex())

# Configure sessions
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = appredis
Session(app)

# Use a mock or simple class for appcache
class SimpleCache:
    def cached(self, timeout=None, key_prefix='view/%s'):
        def decorator(f):
            return f # No-op for now
        return decorator

appcache = SimpleCache()

# Initialize credentials for bilibili API.
appcred = None
if appconf['credential']['use_cred']:
    credstore = appconf['credential']
    appcred = Credential(
        sessdata=credstore['sessdata'],
        bili_jct=credstore['bili_jct'],
        buvid3=credstore['buvid3'],
        dedeuserid=credstore['dedeuserid'],
        ac_time_value=credstore['ac_time_value']
    )

##########################################
# Util functions
##########################################

from opencc import OpenCC
_cc_instance = None

def get_cc():
    global _cc_instance
    if _cc_instance is None:
        _cc_instance = OpenCC('s2twp')
    return _cc_instance

def translate_text(text, enabled=None):
    """Translate text using OpenCC if enabled by the user."""
    if not text or not isinstance(text, str):
        return text
    
    # Check if OpenCC is enabled (explicitly passed or via cookie)
    is_enabled = enabled if enabled is not None else (request.cookies.get('opencc') == '1')
    
    if is_enabled:
        return get_cc().convert(text)
    return text

def detect_theme():
    """Determine the theme of the users' request."""
    theme = request.args.get('theme') or request.cookies.get('theme') or appconf['display']['default_theme']
    return theme

async def render_template_with_theme(fp, **kwargs):
    """Render a template with theming support."""
    t = detect_theme()

    dark_theme = request.cookies.get('dark-theme') == '1'
    opencc_enabled = request.cookies.get('opencc') == '1'
    
    return await render_template(f'themes/{t}/{fp}', dark_mode=dark_theme,
                           opencc_enabled=opencc_enabled,
                           proxy_status=appconf['proxy'],
                           **appconf['site'],
                           **kwargs)