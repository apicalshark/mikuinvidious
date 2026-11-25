# 組態參照

MikuInvidious 可透過 `config.toml` 設定檔（建議方式）或環境變數進行組態配置。設定檔設定（`config.toml`）會優先覆寫環境變數預設值，唯一的例外是 `SITE_URL`（覆寫 `site.site_url`）與 `DEFAULT_ACCENT_COLOR` / `DEFAULT_ACCENT`（覆寫 `display.default_accent`），若於環境變數中設定將優先套用。

---

## `[site]`

網站識別、安全標記與功能開關設定。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | 實例對外公開的網站名稱（請注意在預設的 modern 主題中，頂部商標文字依然固定硬編碼為 MikuInvidious）。 |
| `site_url` | `SITE_URL` | `http://localhost:8888` | 實例的完整對外公開網址（含協定 `http://` 或 `https://`）。Cookie 的 `Secure` 標記會根據 `site_url` 是否以 `https://` 開頭自動判斷，亦可在 `[quart]` 中透過 `SESSION_COOKIE_SECURE` 明確覆寫。 |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | 修改後之原始碼儲存庫連結（若修改了程式碼，為符合 AGPL-3.0 規範必須提供）。未修改請設為 `false`。 |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | 設為 `false` 時於前端隱藏下載按鈕（媒體代理本身仍維持運作）。 |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | 單軌下載大小上限（MB），超出此容量之任務將被拒絕。 |
| `max_download_disk_mb` | `MAX_DOWNLOAD_DISK_MB` | `4096` | 已完成下載任務保留之總磁碟空間上限（MB），超出時會自動淘汰最舊檔案。 |
| `site_show_unsafe_error_response` | `SITE_SHOW_UNSAFE_ERROR_RESPONSE` | `false` | 設為 `true` 時直接於介面展示後端詳細錯誤回應。**僅供本機偵錯使用。** |
| `nyaa_bangumi` | `NYAA_BANGUMI` | `false` | 是否於番劇頁面啟用 Nyaa.si BT 種子搜尋（在 `compose.yml` 中預設為 `true`）。 |
| `robots_policy` | `ROBOTS_POLICY` | `strict` | 搜尋引擎爬蟲政策：`strict`（阻擋所有）、`relaxed`（允許專欄與搜尋）或 `PLEASE_INDEX_EVERYTHING`。 |

---

## `[server]`

Web 伺服器核心設定（Granian / Quart）。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `host` | `SERVER_HOST` | `0.0.0.0` | 監聽 IP 位址（`::` 支援 IPv4/IPv6 雙棧）。 |
| `port` | `SERVER_PORT` | `8888` | 監聽埠號（Docker 容器內為 8080）。 |
| `secret_key` | `QUART_SECRET_KEY` | `（暫態產生）` | 用於簽署工作階段 Cookie 與 CSRF 權杖的密鑰。建議於正式環境設定以保留登入與工作階段狀態。 |
| `debug` | `QUART_DEBUG` | `false` | 啟用 Quart 偵錯模式、程式碼熱重載與 `DEBUG` 日誌層級。 |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Granian 請求存取日誌。預設停用，因媒體串流與輪詢會造成日誌洪泛。 |
| `monitor_fd` | `MONITOR_FD` | `false` | 啟用檔案描述子（File Descriptor）洩漏監控。 |
| `（無）` | `LOG_COLOR` | `true` | 控制 ANSI 彩色日誌輸出。設為 `false`、`0`、`no`、`never` 或 `off` 可停用色彩。 |
| `（無）` | `NO_COLOR` | `（未設定）` | 標準 [no-color.org](https://no-color.org/) 覆寫：設為任何非空值時停用 ANSI 彩色日誌。 |

> [!NOTE]
> **工作階段密鑰與行程黏性（Process Affinity）**：`miku.core.app.create_app()` 會優先使用 `[server].secret_key`（或 `QUART_SECRET_KEY`）；若未設定，則於每次啟動時隨機產生一組 32 位元組的暫態金鑰（`os.urandom(32).hex()`），重啟後現有工作階段與 CSRF 權杖將失效。若執行多個工作行程且未共用金鑰，各行程產生的密鑰不同會導致 Cookie 被拒絕，建議於正式環境設定 `QUART_SECRET_KEY` 或維持單一工作行程。

---

## `[display]`

佈景主題與多國語言偏好。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `default_theme` | （無） | `modern` | 新訪客預設主題（`modern` 為目前維護中的 Tailwind CSS 主題）。 |
| `default_accent` | `DEFAULT_ACCENT_COLOR`, `DEFAULT_ACCENT` | `#a85383` | 預設 Material You 種子強調色十六進位色碼（`#rgb`、`#rrggbb` 或 `#rrggbbaa`）。環境變數優先於 `config.toml`。 |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | 缺乏語系查詢參數、Cookie 或瀏覽器標頭時的回退語言。 |
| `supported_locales` | `SUPPORTED_LOCALES` | （自動發現） | 允許提供的語系清單（以逗號分隔，例如 `en,zh-TW,zh-CN,ja`）。未設定則提供 `locales/` 下的所有語系。 |

---

## `[live]`

直播串流格式策略。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | 設為 `false` 時優先提供 FLV，HLS master 作為備援；設為 `true` 則反轉優先級。 |

---

## `[dash]`

DASH CDN 鏡像合成與區域偏好設定。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `cdn_region` | `DASH_CDN_REGION` | `overseas_hk_tw` | DASH 主機重寫之區域鏡像預設集：`overseas_hk_tw`（海外＋港台 Tier 1）、`overseas`（歐美海外 Tier 1）、`hk_tw`（港台 Tier 1）、`mainland`（中國大陸 Tier 1）、`auto`（依被動 EWMA 吞吐量排序之廣域池）或 `none`（停用鏡像合成）。 |
| `custom_cdn_mirrors` | `DASH_CUSTOM_CDN_MIRRORS` | `[]` | 選用的自訂 Bilibili CDN 主機名稱清單，會優先置於 Tier 1（`config.toml` 使用 TOML 陣列，環境變數以逗號分隔）。非白名單網域會被忽略。 |

---

## `[credential]`

用於解鎖更高畫質（如 1080P60、4K 超高畫質）、大會員付費番劇或降低資料中心 IP 風險控管機率的身分憑證。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `use_cred` | `USE_CRED` | `false` | 憑證功能總開關。若填入 `sessdata` 則自動啟動。 |
| `sessdata` | `SESSDATA` | （無） | Bilibili `SESSDATA` Cookie。 |
| `bili_jct` | `BILI_JCT` | （無） | Bilibili `bili_jct` CSRF 權杖。 |
| `buvid3` | `BUVID3` | （無） | Bilibili `buvid3` Cookie。 |
| `buvid4` | `BUVID4` | （無） | Bilibili `buvid4` Cookie。 |
| `dedeuserid` | `DEDEUSERID` | （無） | Bilibili `DedeUserID`。 |
| `ac_time_value` | `AC_TIME_VALUE` | （無） | Bilibili 本機更新權杖（`window.localStorage.ac_time_value`）。 |

> [!TIP]
> **憑證加密儲存**：可透過 libsodium XSalsa20-Poly1305（PyNaCl `SecretBox`）進行加密。為避免敏感 Cookie 洩漏至終端機歷史紀錄中，建議透過互動式輸入傳遞值：
>
> ```bash
> read -rs SESSDATA && python tools/encrypt_secrets.py --encrypt SESSDATA "$SESSDATA"
> ```
>
> 必須先產生 32 位元組的主密鑰並匯出為 `SECRETS_MASTER_KEY`。

---

## `[proxy]`

對外網路代理設定，用於將伺服器流量經由家用或 VPN 節點轉發。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `proxy_url` | `HTTP_PROXY` / `HTTPS_PROXY` | （無） | SOCKS5 或 HTTP 代理網址（例如 `socks5://127.0.0.1:1080`）。對容易遭遇 HTTP 412 / `-352` 風控的資料中心主機不可或缺。 |

---

## `[render]`

伺服器端渲染轉換設定。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `use_pandoc` | `USE_PANDOC` | `false` | 設為 `true` 時使用系統安裝的 `pandoc` 轉換專欄文章。 |
| `article_allowed_formats` | `ARTICLE_ALLOWED_FORMATS` | `markdown,plain,html` | 允許 Pandoc 進行轉換的來源格式清單。 |

---

## `[bili]`

上游 Bilibili 客戶端標頭與環境參數設定，用於圖片代理、直播串流與上游 API 互動。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `user_agent` | `BILI_USER_AGENT` | `DEFAULT_APP_UA` | Bilibili App 請求預設 User-Agent（Android BiliDroid）。請注意：DASH CDN 媒體串流一律強制傳送桌面 Chrome UA（`build_cdn_headers()`）。 |
| `referer` | `BILI_REFERER` | `https://www.bilibili.com` | 上游 Referer 標頭。 |
| `env` | `BILI_ENV` | `prod` | 上游 API 環境。 |
| `app_key` | `BILI_APP_KEY` | `android64` | App 金鑰識別字串。 |
| `ip_region` | `BILI_IP_REGION` | `CN` | 路由地區標頭。 |
| `legal_region` | `BILI_LEGAL_REGION` | `CN` | 法規遵循地區標頭。 |

---

## `[redis]`

Redis 資料庫連線組態。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `redis_url` | `REDIS_URL` | （無） | 完整 Redis 連線字串（例如 `redis://127.0.0.1:6379`），覆寫以下主機與帳密設定。 |
| `host` | `REDIS_HOST` | `localhost` | Redis 伺服器主機名稱。 |
| `port` | `REDIS_PORT` | `6379` | Redis 伺服器連接埠。 |
| `username` | `REDIS_USERNAME` | （無） | Redis 使用者名稱（若啟用 ACL）。 |
| `password` | `REDIS_PASSWORD` | （無） | Redis 連線密碼。 |

---

## `[cache]`

Redis 頁面快取存留時間（以分鐘為單位），設為 `0` 即可完全停用該路由快取。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `space_minutes` | `SPACE_CACHE_MINUTES` | `5` | `/space/<mid>` 第一頁快取，與 JSON 端點共用。 |
| `space_json_minutes` | `SPACE_JSON_CACHE_MINUTES` | `5` | `/space/<mid>/json` 摘要快取，與 HTML 空間頁共用。 |
| `video_minutes` | `VIDEO_CACHE_MINUTES` | `15` | `/video/<vid>` 資訊快取（留言區永保即時動態）。 |
| `bangumi_minutes` | `BANGUMI_CACHE_MINUTES` | `60` | `/bangumi/view/<ssid>` 番劇季度資訊快取。 |
| `author_minutes` | `AUTHOR_CACHE_MINUTES` | `30` | `/author/<mid>` 創作者概要快取。 |
| `article_minutes` | `ARTICLE_CACHE_MINUTES` | `30` | `/read/<cid>` 與 `/opus/<cid>` 文章解析結果快取。 |
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | `30` | `/audio/<auid>` 與 `/audio_list/<amid>` 的音訊及歌單中繼資料快取。 |
| `home_minutes` | `HOME_CACHE_MINUTES` | `30` | `/` 首頁熱門推薦快取。 |

所有命中快取的回應皆會帶有 `X-Cache: HIT` 標頭，未命中則標記 `X-Cache: MISS`。

---

## `[rate_limit]`

針對客戶端 IP 位址的滑動視窗速率限制（單工作程序預設採用有界記憶體儲存，亦可切換為 Redis 後端並在 Redis 異常時自動退避至記憶體 30 秒）。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `enabled` | `RATE_LIMIT_ENABLED` | `false`（程式預設）/ `true`（`config.toml.sample`、`compose.yml`） | 速率限制器總開關。 |
| `backend` | `RATE_LIMIT_BACKEND` | `memory` | 速率限制儲存後端：`memory`（行程內有界計數器，建議單工作程序使用）或 `redis`（跨工作程序共用 Redis 計數器，異常時自動退避至記憶體 30 秒）。 |
| `trusted_proxies` | `TRUSTED_PROXIES` | `127.0.0.1/8,::1/128` | 以逗號分隔之信任反向代理 IP 位址或 CIDR 網段，僅信任清單內對端所帶入之 `X-Forwarded-For`（`get_client_ip()`）。 |
| `limits.<profile>.limit` | `RATE_LIMIT_<PROFILE>_LIMIT` | 見下方 | 各設定檔 `<profile>`（`strict`、`normal`、`loose`、`search`、`proxy`、`auth`）於單一時間視窗內允許之最大請求數。 |
| `limits.<profile>.window` | `RATE_LIMIT_<PROFILE>_WINDOW` | 見下方 | 各設定檔 `<profile>` 之滑動時間視窗長度（秒）。 |

預設設定檔限制（`[rate_limit.limits.<profile>]`）：

- `strict`：`10` 次請求 / `60` 秒（`RATE_LIMIT_STRICT_LIMIT`、`RATE_LIMIT_STRICT_WINDOW`）
- `normal`：`60` 次請求 / `60` 秒（`RATE_LIMIT_NORMAL_LIMIT`、`RATE_LIMIT_NORMAL_WINDOW`）
- `loose`：`300` 次請求 / `60` 秒（`RATE_LIMIT_LOOSE_LIMIT`、`RATE_LIMIT_LOOSE_WINDOW`）
- `search`：`20` 次請求 / `60` 秒（`RATE_LIMIT_SEARCH_LIMIT`、`RATE_LIMIT_SEARCH_WINDOW`）
- `proxy`：`100` 次請求 / `60` 秒（`RATE_LIMIT_PROXY_LIMIT`、`RATE_LIMIT_PROXY_WINDOW`）
- `auth`：`5` 次請求 / `300` 秒（`RATE_LIMIT_AUTH_LIMIT`、`RATE_LIMIT_AUTH_WINDOW`）

> [!WARNING]
> **反向代理信任（`TRUSTED_PROXIES`）**：`rate_limit.py` **僅**採信符合 `trusted_proxies` 的對端標頭（預設為本機 loopback `127.0.0.1/8,::1/128`，私有網路**不會**被隱式信任）。於 Docker 中搭配 Caddy 或 Nginx 部署時，請將代理容器網段加入 `TRUSTED_PROXIES`（例如 `compose.yml` 將 `miku_net` 固定為 `172.30.0.0/24` 並設定 `TRUSTED_PROXIES=127.0.0.1/8,::1/128,172.30.0.0/24`），並確保邊緣代理覆寫或剝除不受信任的 `X-Forwarded-For`。

---

## 圖片本機磁碟快取（環境變數設定）

由 `python/miku/media/image_cache.py` 提供的 `/proxy/pic/*` 本機磁碟快取可透過環境變數進行調節：

| 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `IMAGE_CACHE_DIR` | `data/img_cache` | 儲存圖片快取的本機目錄。在 Docker 中掛載為 `img_cache` 儲存卷。不可寫入時退避至系統暫存目錄。 |
| `IMAGE_CACHE_MAX_SIZE_MB` | `2048` | 快取容量上限（MB）。超出門檻時自動觸發 LRU 修剪，降至上限的 80%。 |
| `IMAGE_CACHE_TTL_DAYS` | `7` | 快取圖片保留天數。 |

---

## 框架預設值與逾時設定（`python/miku/core/config.py`）

為確保長時間視訊播放不發生斷線，Quart 預設配有超長逾時：

- `RESPONSE_TIMEOUT = 10800`（3 小時）
- `BODY_TIMEOUT = 10800`（3 小時）
- `SESSION_COOKIE_SAMESITE = "Lax"`
