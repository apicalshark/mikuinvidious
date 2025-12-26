# Copyright (C) 2023 MikuInvidious Team
# 
# MikuInvidious is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
# 
# MikuInvidious is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with MikuInvidious. If not, see <http://www.gnu.org/licenses/>.

import os
import redis
import toml
from flask import request, render_template, Flask
from flask_caching import Cache
from bilibili_api import Credential
from refresher import renew_cookies

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
    "flask": {},
    "twisted": {
        "host": os.environ.get("TWISTED_HOST", "0.0.0.0"),
        "port": int(os.environ.get("TWISTED_PORT", 8888)),
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
    "display": {
        "default_theme": os.environ.get("DEFAULT_THEME", "modern"),
    },
    "redis": {
        "host": os.environ.get("REDIS_HOST", "localhost"),
        "port": int(os.environ.get("REDIS_PORT", 6379)),
        "username": os.environ.get("REDIS_USERNAME"),
        "password": os.environ.get("REDIS_PASSWORD"),
    },
    "admin": {
        "username": os.environ.get("ADMIN_USERNAME"),
        "password": os.environ.get("ADMIN_PASSWORD"),
        "secret_key": os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex()),
    }
}

if os.path.exists('config.toml'):
    deep_update(appconf, toml.load('config.toml'))

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

# Initialize the flask app.
app = Flask('app')
app.config.from_mapping(appconf['flask'])

# And also configure the flask_cache module.
appcache = Cache(app, config={'CACHE_TYPE': 'RedisCache', 'CACHE_REDIS': appredis})

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
    # Note: The automatic cookie refresher which writes to config.toml is not compatible with a serverless environment.
    # You will need to manually update your credential environment variables when they expire.

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

    return render_template(f'themes/{t}/{fp}', dark_mode=dark_theme,
                           opencc_enabled=opencc_enabled,
                           proxy_status=appconf['proxy'],
                           **appconf['site'],
                           **kwargs)