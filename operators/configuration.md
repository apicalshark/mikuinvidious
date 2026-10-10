# Configuration reference

The system is configured via `config.toml` or environment variables.
**Environment variables always take precedence over the config file.**
Docker deployments use environment variables; manual deployments use `config.toml`.

## `[site]` identity

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | Site name shown in the header |
| `site_url` | `SITE_URL` | `https://example.org` | Public URL, used for metadata and link generation |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | Modified source repo URL if you changed the code (AGPL requirement) |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | Disabling only hides the download button; it won't stop skilled users |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | Per-track download size limit (MB) |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | Show detailed errors (may leak sensitive info; debug only) |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `true` | Nyaa search toggle on bangumi pages |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | `strict` (block all indexing), `relaxed` (allow articles and search), `PLEASE_INDEX_EVERYTHING` (use with care) |

## `[server]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | Listening interface (`localhost` for local-only, `::` for dual-stack) |
| `port` | `SERVER_PORT` | `8888` | App listen port (8080 inside Docker) |
| `secret_key` | `QUART_SECRET_KEY` | random | Session encryption key. **Production sites must set a fixed value**, or every restart invalidates all sessions |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Per-request Granian access log (very noisy, off by default) |

## `[display]` UI

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `default_theme` | — | `modern` | Default theme for new visitors (only `modern` is officially supported) |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | Fallback locale when no `?lang=` param, cookie, or browser-language match |
| `supported_locales` | `SUPPORTED_LOCALES` | auto-detect | Locale allowlist, e.g. `en,zh-TW,ja`; unset means all enabled |

## `[live]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | `false` = FLV first with HLS fallback; `true` reverses it. One format per room, never switched mid-stream |

## `[credential]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | Master switch |
| `sessdata` / `bili_jct` / `buvid3` / `buvid4` / `dedeuserid` | same name, uppercased | empty | Bilibili cookies — see [Credentials](credentials) for how to obtain them |
| `ac_time_value` | `AC_TIME_VALUE` | empty | Refresh token from Bilibili's `localStorage` |

Sensitive values should be stored encrypted with libsodium
(`tools/encrypt_secrets.py`) — see [Credentials](credentials).

## `[proxy]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY` / `http_proxy` | empty | Full SOCKS5/HTTP egress URL, e.g. `socks5://127.0.0.1:1080`. The media proxy itself is always on; this only decides whether traffic to Bilibili goes through a proxy |

## `[render]` article rendering

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | Render articles with Pandoc (must be installed on the host) |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | Source formats Pandoc may convert from |

## `[redis]`, `[cache]`, `[rate_limit]`

- Redis: setting `redis_url` (`REDIS_URL`) overrides host/port/credentials. Redis is
  required — the app won't start without it.
- Cache TTLs: see [Caching](caching). All in minutes; `0` disables that route's cache.
- Rate limiting: see [Rate limiting](rate-limiting). Off by default.

## `[quart]`, `[bili]`

- `[quart]` settings pass straight through to the Quart framework
  (e.g. `TEMPLATES_AUTO_RELOAD = true`). Streaming timeouts are fixed at
  10800 seconds (3 hours) so long videos play to the end.
- `[bili]` tunes the UA, referer, app_key and other headers used against the
  Bilibili API. Rarely needs changing.
