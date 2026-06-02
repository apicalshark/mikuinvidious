# Configuration Reference

MikuInvidious can be configured using a `config.toml` file or through environment variables. Environment variables always take precedence over the values in the configuration file.

This document provides a comprehensive reference for all available options.

## `[site]`
Settings related to the site's identity and features.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | The public name of your instance. |
| `site_url` | `SITE_URL` | `https://example.org` | The public URL of your instance. Used for metadata and links. |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | A boolean (`true`/`false`) to show a link to the modified source code. |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | A boolean (`true`/`false`) to enable or disable video download buttons. |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | A boolean (`true`/`false`) to show detailed, potentially unsafe error messages. **Use with caution.** |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `true` | A boolean (`true`/`false`) to enable or disable Nyaa search in Bangumi view. |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | Controls the `robots.txt` policy. Can be `strict` (disallow all), `relaxed` (allow articles and search), or `PLEASE_INDEX_EVERYTHING` (use with extreme caution). |

---

## `[server]`
Configuration for the web server.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `host` | `SERVER_HOST` | `0.0.0.0` | The IP address the application server binds to. |
| `port` | `SERVER_PORT` | `8888` | The port the application server binds to. |
| `secret_key` | `QUART_SECRET_KEY` | (Random Hex) | A long, random string used to secure user sessions. If not set, a random 24-character hex string is generated at startup. **It is highly recommended to set a persistent key.** |

---

## `[display]`
Settings related to the user interface and themes.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `default_theme` | (None) | `modern` | The default theme to use for new visitors. |

---

## `[credential]`
Allows the instance to make authenticated requests to Bilibili, which can provide access to higher-quality streams or content.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `use_cred` | `USE_CRED` | `false` | A boolean (`true`/`false`) to enable or disable the use of authenticated credentials. |
| `sessdata` | `SESSDATA` | (None) | Your `SESSDATA` cookie value from Bilibili. |
| `bili_jct` | `BILI_JCT` | (None) | Your `bili_jct` cookie value from Bilibili. |
| `buvid3` | `BUVID3` | (None) | Your `buvid3` cookie value from Bilibili. |
| `buvid4` | `BUVID4` | (None) | Your `buvid4` cookie value from Bilibili. |
| `dedeuserid` | `DEDEUSERID` | (None) | Your `DedeUserID` cookie value from Bilibili. |
| `ac_time_value` | `AC_TIME_VALUE` | (None) | Your `ac_time_value` refresh token from Bilibili local storage. |

---

## `[proxy]`
Configures the use of a proxy (typically SOCKS5) for all outgoing requests to Bilibili. **This is essential for the application to function.**

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `use_proxy` | `NO_PROXY` | `true` | A boolean (`true`/`false`) to enable or disable the proxy. **Note:** This is inverted in the environment variable; setting `NO_PROXY=true` or `NO_PROXY=1` sets `use_proxy` to `false`. |
| `proxy_url` | `HTTP_PROXY` or `http_proxy` | (None) | The full URL of the SOCKS5 proxy. Example: `socks5://127.0.0.1:1080`. |

---

## `[render]`
Settings for rendering Bilibili articles.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `use_pandoc` | `USE_PANDOC` | `false` | A boolean (`true`/`false`) to enable the use of Pandoc for article rendering. |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS`| `markdown,plain,html` | A comma-separated list of formats that Pandoc is allowed to convert from. |

---

## `[redis]`
Configuration for connecting to the Redis database, which is required for caching and sessions.

| Key | Environment Variable | Default | Description |
| --- | --- | --- | --- |
| `redis_url` | `REDIS_URL` | (None) | The full connection URL for Redis. Example: `redis://127.0.0.1:6379`. **If set, this overrides all other Redis keys.** |
| `host` | `REDIS_HOST` | `localhost` | The hostname or IP address of the Redis server. |
| `port` | `REDIS_PORT` | `6379` | The port of the Redis server. |
| `username` | `REDIS_USERNAME` | (None) | The username for Redis authentication. |
| `password` | `REDIS_PASSWORD` | (None) | The password for Redis authentication. |

---

## `[quart]`
This section can be used to pass any specific configuration options directly to the Quart framework (e.g., `TEMPLATES_AUTO_RELOAD = true`). These are advanced settings.

**Hardcoded Timeouts:**
The application sets the following Quart timeouts to **10800 seconds (3 hours)** to support long-form streaming:
- `RESPONSE_TIMEOUT`
- `BODY_TIMEOUT`
