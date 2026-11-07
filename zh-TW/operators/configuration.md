# 組態參照

MikuInvidious 可透過 `config.toml` 設定檔（建議方式）或環境變數進行組態配置。設定檔設定（`config.toml`）會優先覆寫環境變數預設值，唯一的例外是 `SITE_URL`，若於環境變數中設定，將覆蓋 `site.site_url`。

---

## `[site]`
網站識別、安全標記與功能開關設定。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `site_name` | `SITE_NAME` | `MikuInvidious` | 實例對外公開的網站名稱。 |
| `site_url` | `SITE_URL` | `http://localhost:8888` | 實例的完整對外公開網址（含協定 `http://` 或 `https://`）。Cookie 的 `Secure` 標記會根據 `site_url` 是否以 `https://` 開頭自動判斷，亦可在 `[quart]` 中透過 `SESSION_COOKIE_SECURE` 明確覆寫。 |
| `site_modified_source_code_url` | `SITE_MODIFIED_SOURCE_CODE_URL` | `false` | 修改後之原始碼儲存庫連結（若修改了程式碼，為符合 AGPL-3.0 規範必須提供）。未修改請設為 `false`。 |
| `site_allow_download` | `SITE_ALLOW_DOWNLOAD` | `true` | 設為 `false` 時於前端隱藏下載按鈕（媒體代理本身仍維持運作）。 |
| `max_download_size_mb` | `MAX_DOWNLOAD_SIZE_MB` | `1024` | 單軌下載大小上限（MB），超出此容量之任務將被拒絕。 |
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
| `debug` | `QUART_DEBUG` | `false` | 啟用 Quart 偵錯模式與程式碼熱重載。 |
| `access_log` | `SERVER_ACCESS_LOG` | `false` | Granian 請求存取日誌。預設停用，因媒體串流與輪詢會造成日誌洪泛。 |
| `monitor_fd` | `MONITOR_FD` | `false` | 啟用檔案描述子（File Descriptor）洩漏監控。 |

> [!NOTE]
> **暫態工作階段密鑰機制**：基於安全設計，`shared.py` 於每次應用程式啟動時隨機產生一組 32 位元組的金鑰（`os.urandom(32).hex()`）。重新啟動伺服器會刻意使所有使用者的工作階段 Cookie 失效；靜態設定檔中的固定密鑰會被忽略。

---

## `[display]`
佈景主題與多國語言偏好。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `default_theme` | （無） | `modern` | 新訪客預設主題（`modern` 為目前維護中的 Tailwind CSS 主題）。 |
| `default_locale` | `DEFAULT_LOCALE` | `zh-CN` | 缺乏語系查詢參數、Cookie 或瀏覽器標頭時的回退語言。 |
| `supported_locales` | `SUPPORTED_LOCALES` | （自動發現） | 允許提供的語系清單（以逗號分隔，例如 `en,zh-TW,zh-CN,ja`）。未設定則提供 `locales/` 下的所有語系。 |

---

## `[live]`
直播串流格式策略。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `prefer_hls` | `LIVE_PREFER_HLS` | `false` | 設為 `false` 時優先提供 FLV，HLS master 作為備援；設為 `true` 則反轉優先級。 |

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
> **憑證加密儲存**：可透過 libsodium XSalsa20-Poly1305（PyNaCl `SecretBox`）執行 `python tools/encrypt_secrets.py --encrypt SESSDATA "你的 Cookie"` 進行加密。必須先產生 32 位元組的主密鑰並匯出為 `SECRETS_MASTER_KEY`。

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
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | `30` | `/audio/<auid>` 音訊資訊與串流快取。 |
| `home_minutes` | `HOME_CACHE_MINUTES` | `30` | `/` 首頁熱門推薦快取。 |

所有命中快取的回應皆會帶有 `X-Cache: HIT` 標頭，未命中則標記 `X-Cache: MISS`。

---

## `[rate_limit]`
基於 Redis 權杖桶的客戶端 IP 滑動視窗速率限制。

| 設定鍵值 | 對應環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- | :--- |
| `enabled` | `RATE_LIMIT_ENABLED` | `false` | 速率限制器總開關。 |
| `trusted_proxies` | `TRUSTED_PROXIES` | `""` | 以逗號分隔之信任反向代理 IP 位址或 CIDR 網段（例如 `172.18.0.0/16`）。 |

> [!WARNING]
> **私有網路預設信任注意事項**：`rate_limit.py` 會自動採信來自本機 loopback 與私有網路範圍（如 Docker 內部虛擬橋接網路）的代理標頭。請務必確保只有受控的反向代理（Caddy）可以存取應用程式的 8080 埠號，並確保反向代理重寫 `X-Real-IP`。

---

## 圖片本機磁碟快取（環境變數設定）
由 `python/image_cache.py` 提供的 `/proxy/pic/*` 本機磁碟快取可透過環境變數進行調節：

| 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `IMAGE_CACHE_DIR` | `data/img_cache` | 儲存圖片快取的本機目錄。在 Docker 中掛載為 `img_cache` 儲存卷。不可寫入時退避至系統暫存目錄。 |
| `IMAGE_CACHE_MAX_SIZE_MB` | `2048` | 快取容量上限（MB）。超出門檻時自動觸發 LRU 修剪，降至上限的 80%。 |
| `IMAGE_CACHE_TTL_DAYS` | `7` | 快取圖片保留天數。 |

---

## 框架預設值與逾時設定（`python/shared.py`）
為確保長時間視訊播放不發生斷線，Quart 預設配有超長逾時：
- `RESPONSE_TIMEOUT = 10800`（3 小時）
- `BODY_TIMEOUT = 10800`（3 小時）
- `SESSION_COOKIE_SAMESITE = "Lax"`
