# 組態參照

本系統可透過 `config.toml` 或環境變數進行組態，**環境變數的優先順序高於組態檔**。Docker 部署以環境變數為主，手動部署以 `config.toml` 為主。

## `[site]` 站點識別

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | 站點名稱，顯示於頂端列 |
| `site_url` | `SITE_URL` | `https://example.org` | 對外網址，用於詮釋資料與連結產生 |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | 若曾修改程式碼，請填寫修改版儲存庫網址（AGPL 授權要求） |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | 關閉此選項僅隱藏下載按鈕，無法阻止具備技術能力的使用者下載 |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | 每條下載軌道的大小上限（MB） |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | 顯示詳細錯誤訊息（可能包含敏感資訊，僅供除錯使用） |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `true` | 番劇頁面的 Nyaa 搜尋功能開關 |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | `strict`（禁止全部索引）、`relaxed`（允許文章與搜尋頁）、`PLEASE_INDEX_EVERYTHING`（請審慎使用） |

## `[server]` 伺服器

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | 監聽的網路介面（僅本機使用 `localhost`，IPv4／IPv6 雙棧使用 `::`） |
| `port` | `SERVER_PORT` | `8888` | 應用伺服器監聽連接埠（Docker 內部為 8080） |
| `secret_key` | `QUART_SECRET_KEY` | 隨機產生 | 工作階段加密金鑰。**正式站點務必設定固定值**，否則每次重新啟動將導致全部工作階段失效 |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Granian 的每請求存取紀錄（輸出量大，預設關閉） |

## `[display]` 介面

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `default_theme` | — | `modern` | 新訪客的預設主題（目前僅 `modern` 為正式支援） |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | 無 `?lang=` 參數、cookie 或瀏覽器語言匹配時的預設語系 |
| `supported_locales` | `SUPPORTED_LOCALES` | 自動偵測 | 語系白名單，例如 `en,zh-TW,ja`；未設定則全部啟用 |

## `[live]` 直播

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | `false` 表示 FLV 優先、HLS 備援；`true` 則相反。每一直播間僅選用一種格式，播放期間不切換 |

## `[credential]` 憑證

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | 總開關 |
| `sessdata`／`bili_jct`／`buvid3`／`buvid4`／`dedeuserid` | 同名大寫 | 空 | Bilibili Cookie，取得方式見[憑證](credentials.md) |
| `ac_time_value` | `AC_TIME_VALUE` | 空 | Bilibili 主站 `localStorage` 的重新整理權杖 |

敏感數值建議以 libsodium 加密存放（`tools/encrypt_secrets.py`），詳見[憑證](credentials.md)。

## `[proxy]` 代理

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY`／`http_proxy` | 空 | SOCKS5／HTTP 出口的完整網址，例如 `socks5://127.0.0.1:1080`。媒體代理本身一律啟用，此選項僅決定連往 Bilibili 的流量是否經由代理轉發 |

## `[render]` 文章轉譯

| Key | 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | 改用 Pandoc 轉譯文章（主機須預先安裝 pandoc） |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | Pandoc 允許轉換的來源格式 |

## `[redis]`、`[cache]`、`[rate_limit]`

- Redis：設定 `redis_url`（`REDIS_URL`）後將覆寫 host／port／帳號密碼。Redis 為必要元件，未設定將無法啟動。
- 快取 TTL：詳見[快取](caching.md)，單位均為分鐘，`0` 表示停用該路由的快取。
- 速率限制：詳見[速率限制](rate-limiting.md)，預設為關閉。

## `[quart]`、`[bili]`

- `[quart]` 的設定將直接傳遞給 Quart 框架（例如 `TEMPLATES_AUTO_RELOAD = true`）。串流逾時固定為 10800 秒（3 小時），以支援長片完整播放。
- `[bili]` 可調整呼叫 Bilibili API 所用的 UA、referer、app_key 等標頭，一般無需修改。
