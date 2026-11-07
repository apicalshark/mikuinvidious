# Configuration Reference

MikuInvidious can be configured using `config.toml` (recommended) or environment variables. Configuration file settings (`config.toml`) override environment variable defaults, with the sole exception of `SITE_URL`, which overrides `site.site_url` if set in the environment.

---

## `[site]`
Settings related to site identity, security flags, and features.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | The public brand name of your instance (note that in the default modern theme, the header logo text remains hard-coded as MikuInvidious). |
| `site_url` | `SITE_URL` | `http://localhost:8888` | The public URL of your instance, including scheme (`http://` or `https://`). The session cookie `Secure` flag dynamically defaults based on `site_url.startswith("https://")`, but can be explicitly overridden by `SESSION_COOKIE_SECURE` in `[quart]`. |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | Link to modified source code (required by AGPL-3.0 if you modified the code). Set to `false` if unmodified. |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | When `false`, hides video download UI elements (media proxy remains available). |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | Maximum allowed size (in MB) per downloaded track. Jobs exceeding this limit are rejected. |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | When `true`, exposes raw backend error responses directly in the UI. **Use only for local debugging.** |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `false` | Enable or disable Nyaa.si torrent search in the Bangumi view (set to `true` in `compose.yml`). |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | Controls `robots.txt`: `strict` (disallow all), `relaxed` (allows articles/search), or `PLEASE_INDEX_EVERYTHING`. |

---

## `[server]`
Configuration for the web server (Granian / Quart).

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | Host IP address to bind to (`::` for dual-stack IPv4/IPv6). |
| `port` | `SERVER_PORT` | `8888` | Port to bind to (8080 inside Docker). |
| `debug` | `QUART_DEBUG` | `false` | Enables Quart debug mode and auto-reload. |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Granian per-request access log. Disabled by default because media proxying/polling floods application logs. |
| `monitor_fd` | `MONITOR_FD` | `false` | Enables file descriptor leak monitoring. |

> [!NOTE]
> **Ephemeral Session Secret Key & Process Affinity**: For security, `shared.py` generates an ephemeral 32-byte random hex key (`os.urandom(32).hex()`) at every startup. User sessions are intentionally invalidated across server restarts; static secret keys in configuration files are ignored. Because independently started worker processes generate different keys and will reject each other's session cookies (causing intermittent CSRF failures on downloads and language switches), deployments must run a single worker process or enforce strict session affinity (sticky sessions) across backend instances.

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
Authentication credentials for accessing higher video resolutions (e.g., 1080P60, 4K), paid anime episodes, or mitigating datacenter IP risk gating.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | Enable credential usage. Automatically set to `true` if `sessdata` is provided. |
| `sessdata` | `SESSDATA` | (None) | Bilibili `SESSDATA` cookie. |
| `bili_jct` | `BILI_JCT` | (None) | Bilibili `bili_jct` CSRF token. |
| `buvid3` | `BUVID3` | (None) | Bilibili `buvid3` cookie. |
| `buvid4` | `BUVID4` | (None) | Bilibili `buvid4` cookie. |
| `dedeuserid` | `DEDEUSERID` | (None) | Bilibili `DedeUserID`. |
| `ac_time_value` | `AC_TIME_VALUE` | (None) | Bilibili local-storage refresh token (`window.localStorage.ac_time_value`). |

> [!TIP]
> **Encrypted Credentials**: Credentials can be encrypted using libsodium XSalsa20-Poly1305 (via PyNaCl `SecretBox`). To avoid leaking cookies into shell command history, supply the secret via interactive input:
> ```bash
> read -rs SESSDATA && python tools/encrypt_secrets.py --encrypt SESSDATA "$SESSDATA"
> ```
> A 32-byte master key must first be exported as `SECRETS_MASTER_KEY`.

---

## `[proxy]`
Outbound proxy configuration for routing traffic through residential or VPN egresses.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY` / `HTTPS_PROXY` | (None) | SOCKS5 or HTTP proxy URL (e.g., `socks5://127.0.0.1:1080`). Essential for datacenter IPs triggering HTTP 412 / `-352` risk controls. |

---

## `[render]`
Server-side rendering helpers.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | When `true`, uses system `pandoc` to convert articles. |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | Allowed formats Pandoc is permitted to convert from. |

---

## `[bili]`
Upstream Bilibili client headers and platform parameters. Used for image proxying, live streaming, and upstream API interactions.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `user_agent` | `BILI_USER_AGENT` | `DEFAULT_APP_UA` | Default User-Agent for Bilibili app API calls (Android BiliDroid). Note: DASH CDN media streaming always sends web Chrome UA (`build_cdn_headers()`). |
| `referer` | `BILI_REFERER` | `https://www.bilibili.com` | Upstream Referer header for API and media requests. |
| `env` | `BILI_ENV` | `prod` | Upstream API environment. |
| `app_key` | `BILI_APP_KEY` | `android64` | App key identifier for signed API endpoints. |
| `ip_region` | `BILI_IP_REGION` | `CN` | Region header for API routing. |
| `legal_region` | `BILI_LEGAL_REGION` | `CN` | Legal region compliance header. |

---

## `[redis]`
Redis database connection settings.

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
Sliding-window Redis token bucket rate limiting per client IP address.

| Key | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `RATE_LIMIT_ENABLED` | `false` | Master toggle for rate limiting. |
| `trusted_proxies` | `TRUSTED_PROXIES` | `""` | Comma-separated list of IP addresses or CIDR blocks whose forwarding proxy headers (`X-Real-IP`, `X-Forwarded-For`) are trusted. |

> [!WARNING]
> **Implicit Private Forwarder Trust**: `rate_limit.py` automatically trusts loopback peers and private IP addresses (e.g. Docker networks), even when `trusted_proxies` is empty. Ensure your reverse proxy (Caddy) sets `header_up X-Real-IP {remote_host}`.

---

## Image Disk Cache (Environment Variables)
The local disk cache for proxied images (`/proxy/pic/*`) in `python/image_cache.py` is configured via environment variables:

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `IMAGE_CACHE_DIR` | `data/img_cache` | Filesystem path for caching images. Mounted as volume `img_cache` in Docker. Falls back to `tempfile.gettempdir()` if unwritable. |
| `IMAGE_CACHE_MAX_SIZE_MB` | `2048` | Maximum cache size in MB before LRU pruning trims disk usage down to 80%. |
| `IMAGE_CACHE_TTL_DAYS` | `7` | Retention period in days for cached images. |

---

## Framework Defaults & Timeouts (`python/shared.py`)
To support long-form video playback and media streaming without idle connection drops, Quart is configured by default with:
- `RESPONSE_TIMEOUT = 10800` (3 hours)
- `BODY_TIMEOUT = 10800` (3 hours)
- `SESSION_COOKIE_SAMESITE = "Lax"`
