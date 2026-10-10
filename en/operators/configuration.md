# Configuration reference

`config.toml` or environment variables. **Environment variables win.**
Docker deployments use env vars; manual installs use `config.toml`.

## `[site]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | Header brand |
| `site_url` | `SITE_URL` | `https://example.org` | Public URL for metadata and links |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | Modified source repo URL (AGPL requirement if you changed the code) |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | Hides the download button only; does not block downloads |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | Per-track download size cap (MB) |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | Verbose errors; may leak sensitive info (debug only) |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `true` | Nyaa search on bangumi pages |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | `strict` (block all), `relaxed` (articles + search), `PLEASE_INDEX_EVERYTHING` |

## `[server]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | Listen interface (`localhost` = local only, `::` = dual-stack) |
| `port` | `SERVER_PORT` | `8888` | Listen port (8080 inside Docker) |
| `secret_key` | `QUART_SECRET_KEY` | random | Session key. **Set a fixed value in production** or restarts kill all sessions |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Per-request Granian access log (noisy, off by default) |

## `[display]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `default_theme` | — | `modern` | New-visitor theme (only `modern` is supported) |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | Fallback when no `?lang=`, cookie, or browser-language match |
| `supported_locales` | `SUPPORTED_LOCALES` | auto-detect | Allowlist, e.g. `en,zh-TW,ja`; unset = all |

## `[live]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | `false` = FLV first, HLS fallback; `true` reverses it. One format per room, never switched mid-stream |

## `[credential]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | Master switch |
| `sessdata` / `bili_jct` / `buvid3` / `buvid4` / `dedeuserid` | same, uppercased | empty | Bilibili cookies — see [Credentials](credentials) |
| `ac_time_value` | `AC_TIME_VALUE` | empty | Refresh token from Bilibili `localStorage` |

Encrypt secrets with libsodium (`tools/encrypt_secrets.py`) — see
[Credentials](credentials).

## `[proxy]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY` / `http_proxy` | empty | SOCKS5/HTTP egress, e.g. `socks5://127.0.0.1:1080`. The media proxy is always on; this only routes Bilibili traffic through a proxy |

## `[render]`

| Key | Env var | Default | Description |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | Render articles with Pandoc (must be installed) |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | Allowed Pandoc source formats |

## `[redis]`, `[cache]`, `[rate_limit]`

- `redis_url` (`REDIS_URL`) overrides host/port/credentials. Redis is required.
- Cache TTLs: see [Caching](caching). Minutes; `0` disables the route.
- Rate limiting: see [Rate limiting](rate-limiting). Off by default.

## `[quart]`, `[bili]`

- `[quart]` passes through to Quart (e.g. `TEMPLATES_AUTO_RELOAD = true`).
  Streaming timeouts are fixed at 10800 s (3 h).
- `[bili]` tunes UA, referer, app_key, etc. for Bilibili API calls.
