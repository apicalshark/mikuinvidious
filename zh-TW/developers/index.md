# 開發者指南

MikuInvidious 是一套非同步、隱私優先的 Bilibili 網頁前端與媒體代理伺服器，採用 Python 3.15+、Quart (ASGI)、Granian、Redis 與 Caddy 構建。

---

## 技術棧

- **執行環境與框架**：Python 3.15+、[Quart](https://pgjones.gitlab.io/quart/)（現代化非同步 ASGI 框架）、[Granian](https://github.com/emmett-framework/granian)
- **Web 伺服器與反向代理**：[Caddy](https://caddyserver.com/)（負責 TLS 終結、以 `file_server` 直接傳遞 `/static/*` 靜態檔案，並將動態路由轉送給 Granian）
- **資料庫與快取**：[Redis](https://redis.io/)（用於影片播放連結規格化、頁面快取、速率限制與時效憑證儲存；Quart 客戶端工作階段採用客戶端簽名 Cookie）
- **本機 API 封裝**：自建並維護的 `python/api/` 模組，內建 WBI 查詢簽名、`bili_ticket` 產生、Gaia 風控規避與 PGC 自動退避機制
- **前端與播放器**：Tailwind CSS 4、`dash.js`（隨選視訊 DASH v5）、`hls.js` + `mpegts.js`（直播串流）、`media-chrome`
- **選用出口**：Cloudflare WARP（SOCKS5 代理 `:1080`），用於繞過資料中心 IP 風險控制

---

## 程式碼庫架構導覽

| 子系統 | 關鍵模組 | 職責與架構 |
| :--- | :--- | :--- |
| **核心與應用** | `python/app.py`, `python/shared.py`, `python/main.py` | Quart 應用實例化、全域例外處理器、Redis 連線池、HTTP 客戶端管理與組態載入。 |
| **視圖層（Views）** | `python/views.py`, `python/views_*.py` | 路由處理器拆分為 `views_home.py`、`views_video.py`、`views_space.py`、`views_bangumi.py`、`views_live.py`、`views_audio.py`。 |
| **DASH 串流引擎** | `python/dash_*.py` | 模組化串流管線：`dash_mpd.py`、`dash_fetch.py`、`dash_stream.py`、`dash_common.py`、`dash_download_track.py`、`dash_download_job.py`。 |
| **媒體與快取** | `python/proxy.py`, `python/image_cache.py`, `python/live_manager.py` | 圖片代理與本機 LRU 磁碟快取、傳統 MP4 漸進退避代理、直播長連線與心跳封包注入。 |
| **網路與安全防禦** | `python/dns_cache.py`, `python/rate_limit.py`, `python/ip_slots.py`, `python/stream.py` | 非同步 DNS SSRF 防禦、Redis 權杖桶速率限制、外連 IP 併發平衡、原生 TCP Socket 與持久化 TLS 上下文。 |
| **API 封裝** | `python/api/` (`client.py`, `video.py`, `user.py`, `bangumi.py` 等) | 向上游 Bilibili 發起請求之 HTTP 客戶端，處理 WBI 簽名、Ticket 簽章與資料中心 IP 退避。 |
| **範本與靜態資源** | `templates/themes/modern/`, `static/` | Jinja2 網頁範本、Tailwind CSS 樣式表，以及自託管的播放器腳本。 |

---

## 建議閱讀順序

1. [系統架構](architecture) —— 系統宏觀架構、壯麗的請求流程圖與模組完整清單。
2. [播放與下載架構](playback-stack) —— 模組化 DASH 串流機制、Socket 連線複用池、鏡像節點容錯與背景下載。
3. [路由與 API 參照](api-reference) —— 公開網頁端點、媒體代理路徑與 JSON 訂閱摘要。
4. [Bilibili API 封裝](bilibili-api) —— WBI 查詢簽名雜湊演算法、`bili_ticket` 密碼學規範與風控防護。
5. [主題開發](theming) 與 [多國語系](i18n) —— UI 範本設計、Tailwind CSS 4 建置管線與 gettext 翻譯字典。
6. [測試](testing) —— 驗證決策矩陣、迴歸測試集與位元組碼編譯驗證。
