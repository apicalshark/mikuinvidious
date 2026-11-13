# Configuration Reference

MikuInvidious can be configured using `config.toml` (recommended) or environment variables. Configuration file settings (`config.toml`) override environment variable defaults, with the sole exception of `SITE_URL`, which overrides `site.site_url` if set in the environment.

---

## `[site]`
Settings related to site identity, security flags, and features.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | The public name of your instance. |
| `site_url` | `SITE_URL` | `http://localhost:8888` | The public URL of your instance, including actual HTTP/HTTPS scheme. The session cookie `Secure` flag dynamically defaults based on `site_url.startswith("https://")`, but can be explicitly overridden by `SESSION_COOKIE_SECURE` in `[quart]`. |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | Link to modified source code (required by AGPL-3.0 if you modified the code). Set to `false` if unmodified. |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | When `false`, hides video download UI elements (media proxy remains available). |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | Maximum allowed size (in MB) per downloaded track. Jobs exceeding this limit are rejected. |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | When `true`, exposes raw backend error responses directly in the UI. **Use only for local debugging.** |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `false` | Enable or disable Nyaa.si torrent search in the Bangumi view. |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | Controls `robots.txt`: `strict` (disallow all), `relaxed` (allows articles/search), or `PLEASE_INDEX_EVERYTHING`. |

---

## `[server]`
Configuration for the web server (Granian / Quart).

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | Host IP address to bind to (`::` for dual-stack IPv4/IPv6). |
| `port` | `SERVER_PORT` | `8888` | Port to bind to. |
| `debug` | `QUART_DEBUG` | `false` | Enables Quart debug mode and auto-reload. |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Granian per-request access log. Disabled by default because media proxying/polling floods application logs. |
| `monitor_fd` | `MONITOR_FD` | `false` | Enables file descriptor leak monitoring. |

> [!NOTE]
> **Session Secret Key:** `miku.core.app.create_app()` uses `[server].secret_key` (or `QUART_SECRET_KEY`) when configured, and otherwise generates an ephemeral 32-byte random hex key (`os.urandom(32).hex()`) at each startup.

---

## `[display]`
UI themes and internationalization.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `default_theme` | (None) | `modern` | Default theme for new visitors (`modern` is the active Tailwind CSS theme). |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | Fallback UI locale when no query param (`?lang=`), cookie, or `Accept-Language` match. |
| `supported_locales` | `SUPPORTED_LOCALES` | (auto-discover) | Comma-separated list of allowed locales (e.g. `en,zh-CN,zh-TW,ja`). If unset, offers all locales under `locales/`. |

---

## `[live]`
Live stream format policies.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | When `false`, FLV is served first with HLS master as fallback. When `true`, HLS is served first with FLV fallback. |

---

## `[credential]`
Authentication credentials for accessing higher video resolutions (e.g., 1080p+), paid content, or reducing datacenter IP risk-control gating.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | Enable or disable credential usage. Automatically set to `true` if `sessdata` is provided. |
| `sessdata` | `SESSDATA` | (None) | Bilibili `SESSDATA` cookie. |
| `bili_jct` | `BILI_JCT` | (None) | Bilibili `bili_jct` CSRF token. |
| `buvid3` | `BUVID3` | (None) | Bilibili `buvid3` cookie. |
| `buvid4` | `BUVID4` | (None) | Bilibili `buvid4` cookie. |
| `dedeuserid` | `DEDEUSERID` | (None) | Bilibili `DedeUserID`. |
| `ac_time_value` | `AC_TIME_VALUE` | (None) | Bilibili local-storage refresh token (`window.localStorage.ac_time_value`). |

> [!TIP]
> **Encrypted Credentials:** Credentials can be encrypted using libsodium XSalsa20-Poly1305 (via PyNaCl `SecretBox`) with `python tools/encrypt_secrets.py --encrypt SESSDATA "your_cookie"`. A 32-byte master key must first be generated and exported as `SECRETS_MASTER_KEY` (e.g., `export SECRETS_MASTER_KEY=$(python3 -c "import nacl.utils, base64; print(base64.b64encode(nacl.utils.random(32)).decode())")`). The same `SECRETS_MASTER_KEY` must be set in the environment at runtime for the application to decrypt secrets into memory.

---

## `[proxy]`
Outbound network proxy for upstream Bilibili API and CDN traffic.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` | (None) | Proxy URL (e.g. `socks5://127.0.0.1:1080` for Cloudflare WARP or HTTP proxy). Required on datacenter IPs to bypass HTTP 412 / `-352` risk control. Media proxying itself is always active. |

---

## `[render]`
Settings for rendering Bilibili articles using Pandoc.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | When `true`, uses system `pandoc` to convert articles. |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | Allowed formats Pandoc is permitted to convert from. In `config.toml`, specify as a TOML array (e.g., `['markdown', 'plain', 'html']`). In the environment variable, provide a comma-separated string. |

---

## `[bili]`
Upstream Bilibili client headers and platform parameters.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `user_agent` | `BILI_USER_AGENT` | `DEFAULT_APP_UA` | Default User-Agent for Bilibili app API calls (Android BiliDroid). |
| `referer` | `BILI_REFERER` | `https://www.bilibili.com` | Upstream Referer header. |
| `env` | `BILI_ENV` | `prod` | Upstream API environment. |
| `app_key` | `BILI_APP_KEY` | `android64` | App key identifier for signed API endpoints. |
| `ip_region` | `BILI_IP_REGION` | `CN` | Region header for API routing. |
| `legal_region` | `BILI_LEGAL_REGION` | `CN` | Legal region compliance header. |

---

## `[redis]`
Redis database connection settings (used for media caching, rate limiting, and ticket storage; note that Quart sessions are stored in signed client cookies, so flushing Redis does not log out users).

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `redis_url` | `REDIS_URL` | (None) | Full Redis connection string (e.g., `redis://127.0.0.1:6379`). Overrides `host`, `port`, `username`, and `password`. |
| `host` | `REDIS_HOST` | `localhost` | Redis server hostname. |
| `port` | `REDIS_PORT` | `6379` | Redis server port. |
| `username` | `REDIS_USERNAME` | (None) | Redis username (if ACL enabled). |
| `password` | `REDIS_PASSWORD` | (None) | Redis password. |

---

## `[cache]`
TTLs in minutes for Redis-backed page data. Set any TTL to `0` to disable caching for that route.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `space_minutes` | `SPACE_CACHE_MINUTES` | `5` | `/space/<mid>` page 1 payload. Shared with JSON feed. |
| `space_json_minutes` | `SPACE_JSON_CACHE_MINUTES` | `5` | `/space/<mid>/json` feed payload. Shared with HTML space page. |
| `video_minutes` | `VIDEO_CACHE_MINUTES` | `15` | `/video/<vid>` metadata payload (info/tags/related/pages). Comments stay live. |
| `bangumi_minutes` | `BANGUMI_CACHE_MINUTES` | `60` | `/bangumi/view/<ssid>` season metadata payload. |
| `author_minutes` | `AUTHOR_CACHE_MINUTES` | `30` | `/author/<mid>` user overview payload. |
| `article_minutes` | `ARTICLE_CACHE_MINUTES` | `30` | `/read/<cid>` and `/opus/<cid>` parsed article payloads. |
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | `30` | `/audio/<auid>` and `/audio_list/<amid>` metadata payloads. |
| `home_minutes` | `HOME_CACHE_MINUTES` | `30` | `/` homepage feed payload. |

All cached pages emit `X-Cache: HIT` or `X-Cache: MISS`.

---

## `[rate_limit]`
Sliding-window Redis-backed rate limiting per IP address.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `RATE_LIMIT_ENABLED` | `false` | Master toggle for rate limiting. |
| `trusted_proxies` | `TRUSTED_PROXIES` | `""` | Comma-separated list of IP addresses or CIDR blocks whose forwarding proxy headers (`X-Forwarded-For`, etc.) are trusted. Loopback and private peers are also trusted even when this setting is empty. |

> [!WARNING]
> **Implicit Private Forwarder Trust:** `rate_limit.py` automatically trusts loopback peers and addresses classified as private by Python’s `ipaddress` module, even when `trusted_proxies` is empty. Restrict direct access to the application from those peers to controlled proxies, and ensure every trusted proxy overwrites `X-Real-IP` and `X-Forwarded-For` with validated client IP values so clients cannot spoof their rate-limit identity.

---

## Image Disk Cache (Environment Configured)
The local disk cache for proxied images (`/proxy/pic/*`) in `python/miku/media/image_cache.py` is configured via environment variables:

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `IMAGE_CACHE_DIR` | `data/img_cache` | Filesystem path for caching images. Falls back to `miku_img_cache` inside `tempfile.gettempdir()` (e.g. `/tmp/miku_img_cache`) if unwritable. |
| `IMAGE_CACHE_MAX_SIZE_MB` | `2048` | Maximum cache size in MB before LRU pruning trims disk usage to 80%. |
| `IMAGE_CACHE_TTL_DAYS` | `7` | Retention period in days for cached images. |

---

## Framework Defaults & Timeouts (`python/miku/core/config.py`)
To support long-form video playback and media streaming without idle connection drops, Quart is configured by default with:
- `RESPONSE_TIMEOUT = 10800` (3 hours)
- `BODY_TIMEOUT = 10800` (3 hours)
- `SESSION_COOKIE_SAMESITE = "Lax"` (can be overridden via `[quart]` in `config.toml`)