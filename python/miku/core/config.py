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
"""Configuration loading and environment settings management."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import rtoml

from miku.core.fingerprints import DEFAULT_APP_UA

# Cache schema version and freshness windows
CACHE_SCHEMA_VERSION = 1
CACHE_STALE_GRACE_SECONDS = 6 * 3600

# Upstream response headers that must never reach clients
UPSTREAM_RESPONSE_BLACKLIST = frozenset(
    {
        "server",
        "alt-svc",
        "report-to",
        "timing-allow-origin",
        "access-control-allow-origin",
        "cross-origin-embedder-policy",
        "cross-origin-opener-policy",
        "cross-origin-resource-policy",
    }
)


def deep_update(base_dict: dict[str, Any], update_dict: dict[str, Any]) -> None:
    """Recursively merge update_dict into base_dict."""
    for key, value in update_dict.items():
        if isinstance(value, dict) and key in base_dict and isinstance(base_dict[key], dict):
            deep_update(base_dict[key], value)
        else:
            base_dict[key] = value


def _int_env(name: str, default: int) -> int:
    """Parse an int env var, falling back to default on missing/invalid values."""
    try:
        return int(os.environ.get(name, default))
    except TypeError, ValueError:
        return default


def get_default_config() -> dict[str, Any]:
    """Build a default configuration dictionary using environment variables."""
    return {
        "site": {
            "site_name": os.environ.get("SITE_NAME", "MikuInvidious"),
            "site_url": "http://localhost:8888",
            "site_modified_source_code_url": (
                os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "")
                if os.environ.get("SITE_MODIFIED_SOURCE_CODE_URL", "").lower() not in ["false", ""]
                else False
            ),
            "site_allow_download": os.environ.get("SITE_ALLOW_DOWNLOAD", "true").lower() == "true",
            "max_download_size_mb": int(os.environ.get("MAX_DOWNLOAD_SIZE_MB", 1024)),
            "site_show_unsafe_error_response": (
                os.environ.get("SITE_SHOW_UNSAFE_ERROR_RESPONSE", "false").lower() == "true"
            ),
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
            "access_log": os.environ.get("SERVER_ACCESS_LOG", "false").lower() == "true",
        },
        "display": {
            "default_theme": "modern",
            "default_locale": os.environ.get("DEFAULT_LOCALE", "zh-CN"),
            "default_accent": os.environ.get("DEFAULT_ACCENT_COLOR") or os.environ.get("DEFAULT_ACCENT", "#a85383"),
            "supported_locales": os.environ.get("SUPPORTED_LOCALES"),
        },
        "live": {
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
            "proxy_url": (
                os.environ.get("HTTP_PROXY")
                or os.environ.get("http_proxy")
                or os.environ.get("HTTPS_PROXY")
                or os.environ.get("https_proxy")
                or os.environ.get("ALL_PROXY")
                or os.environ.get("all_proxy")
            ),
        },
        "render": {
            "use_pandoc": os.environ.get("USE_PANDOC", "false").lower() == "true",
            "article_allowed_formats": os.environ.get("ARTICLE_ALLOWED_FORMATS", "markdown,plain,html").split(","),
        },
        "bili": {
            "user_agent": os.environ.get("BILI_USER_AGENT", DEFAULT_APP_UA),
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
            "trusted_proxies": os.environ.get("TRUSTED_PROXIES", "127.0.0.1/8,::1/128"),
        },
        "cache": {
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


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load configuration from file and merge with environment variables."""
    conf = get_default_config()

    if config_path is not None and Path(config_path).exists():
        deep_update(conf, rtoml.load(Path(config_path)))
    elif Path("config.toml").exists():
        deep_update(conf, rtoml.load(Path("config.toml")))
    elif Path("../config.toml").exists():
        deep_update(conf, rtoml.load(Path("../config.toml")))

    if "SITE_URL" in os.environ:
        conf["site"]["site_url"] = os.environ["SITE_URL"]

    if "DEFAULT_ACCENT_COLOR" in os.environ:
        conf["display"]["default_accent"] = os.environ["DEFAULT_ACCENT_COLOR"]
    elif "DEFAULT_ACCENT" in os.environ:
        conf["display"]["default_accent"] = os.environ["DEFAULT_ACCENT"]

    # Initialize locale allowlist
    from miku.ui.i18n import set_supported_locales

    set_supported_locales(conf["display"].get("supported_locales"))

    return conf


def compute_asset_version(root_dir: str | Path | None = None) -> str:
    """Compute short version string for static-asset ?v= cache busting."""
    env_commit = os.getenv("GIT_COMMIT", "").strip()
    if env_commit:
        v = "".join(c for c in env_commit[:16] if c.isalnum() or c in "-_")
        if v:
            return v
    env_ver = os.getenv("APP_VERSION", "").strip()
    if env_ver:
        clean_v = env_ver.split(" @ ")[0]
        v = "".join(c for c in clean_v[:16] if c.isalnum() or c in "-_")
        if v:
            return v
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent.parent.parent
    try:
        p = subprocess.run(
            ["git", "-C", str(root_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode == 0 and p.stdout.strip():
            v = "".join(c for c in p.stdout.strip()[:16] if c.isalnum() or c in "-_")
            if v:
                return v
    except Exception:
        pass
    try:
        latest = 0.0
        for dirpath, _, filenames in os.walk(os.path.join(str(root_dir), "static")):
            for fn in filenames:
                try:
                    latest = max(latest, os.path.getmtime(os.path.join(dirpath, fn)))
                except OSError:
                    continue
        if latest:
            return str(int(latest))
    except Exception:
        pass
    return "dev"


def compute_app_version(root_dir: str | Path | None = None) -> tuple[str, str]:
    """Compute application version and commit hash."""
    version = os.getenv("APP_VERSION", "").strip()
    commit = os.getenv("GIT_COMMIT", "").strip()

    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent.parent.parent

    if not version or not commit:
        try:
            if not version:
                p = subprocess.run(
                    ["git", "-C", str(root_dir), "describe", "--tags", "--always", "--dirty"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if p.returncode == 0 and p.stdout.strip():
                    version = p.stdout.strip()

                p_b = subprocess.run(
                    ["git", "-C", str(root_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if p_b.returncode == 0:
                    branch = p_b.stdout.strip()
                    if branch and branch != "HEAD":
                        version = f"{version} @ {branch}" if version else f"@ {branch}"

            if not commit:
                p_c = subprocess.run(
                    ["git", "-C", str(root_dir), "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if p_c.returncode == 0 and p_c.stdout.strip():
                    commit = p_c.stdout.strip()
        except Exception:
            pass

    if not version:
        version = "dev"
    if not commit:
        clean_v = version.split(" @ ")[0]
        if "-g" in clean_v:
            commit = clean_v.split("-g")[-1].split("-")[0]
        elif clean_v != "dev":
            commit = clean_v[:7]
        else:
            commit = ""

    return version, commit


def compute_commit_url(repo_url: str | None, commit: str) -> str:
    """Generate commit browser link according to git hosting platform."""
    if not commit or not repo_url:
        return ""
    repo = repo_url.rstrip("/")
    if "0xacab" in repo or "gitlab" in repo:
        return f"{repo}/-/commit/{commit}"
    return f"{repo}/commit/{commit}"


def cache_minutes(conf: dict[str, Any], key: str, default: int = 5) -> int:
    """Read a [cache] TTL (in minutes); invalid values fall back to default."""
    try:
        return int(conf.get("cache", {}).get(key, default))
    except TypeError, ValueError:
        return default


def downloads_allowed(conf: dict[str, Any] | None = None) -> bool:
    """Return whether downloads are enabled in [site].site_allow_download."""
    if conf is None:
        import miku.state as shared

        conf = shared.appconf
    val = (conf.get("site") or {}).get("site_allow_download", True)
    if isinstance(val, str):
        return val.strip().lower() == "true"
    return bool(val)
