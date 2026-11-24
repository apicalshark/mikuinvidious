# 路由與 API 參照

本文件列出應用程式所提供的所有公開網頁路徑、媒體串流代理端點與輔助 REST API。

---

## 1. 應用程式公開網頁路由

| 路由路徑 | 視圖藍圖模組 | 功能職責說明 |
| :--- | :--- | :--- |
| `/` | `miku/views/home.py` | 首頁與全站熱門推薦動態。 |
| `/search` | `miku/views/home.py` | 全站通用搜尋（影片、番劇、使用者、直播頁籤）。 |
| `/convert?url=<網址>` | `miku/views/home.py` | 伺服器端短網址展開與去追蹤過濾工具。 |
| `/video/<vid>` | `miku/views/video.py` | 影片頁面、中繼資料、播放器、分 P 清單、留言區與純音訊聆聽模式（`?listen=1`）。 |
| `/video/<vid>:<idx>` | `miku/views/video.py` | 指定分 P 影片頁（冒號標記或 `?p=<idx>` 參數）。 |
| `/video/dash/<vid>/<idx>/manifest.mpd` | `miku/dash/mpd.py` | 動態 ISO/IEC 23009-1 DASH 清單（`?fresh=1` 強制刷新、`?audio_only=1` 僅保留音軌、依 `preferred_codec` Cookie 排序，HMAC 簽章保護）。 |
| `/bangumi` | `miku/views/bangumi.py` | 動畫與番劇分類目錄。 |
| `/bangumi/view/<ssid>` | `miku/views/bangumi.py` | 季番詳情、單集網格與 PV 預告花絮。 |
| `/bangumi/play/<epid>` | `miku/views/bangumi.py` | 番劇單集播放介面。 |
| `/bangumi/api/nyaa/<ssid>` | `miku/views/bangumi.py` | Nyaa.si 種子整合查詢 API。 |
| `/space/<mid>` | `miku/views/space.py` | 創作者個人空間與影片投稿列表（`?i=N` 分頁）。 |
| `/space/<mid>/json` | `miku/views/space.py` | 免登入之機器可讀最新投稿 JSON 訂閱摘要。 |
| `/author/<mid>` | `miku/views/space.py` | 輕量創作者個人簡介頁面。 |
| `/live`, `/live/<room_id>` | `miku/views/live.py` | 直播目錄與直播間播放器。 |
| `/live/chat/<room_id>` | `miku/views/live.py` | 即時直播聊天室彈幕 SSE 串流。 |
| `/read/<cid>`, `/opus/<cid>` | `miku/views/space.py` | 純淨專欄文章與動態圖文閱讀器。 |
| `/audio/<auid>` | `miku/views/audio.py` | 單曲播放介面（含歌詞同步）。 |
| `/audio_list/<amid>` | `miku/views/audio.py` | 音樂專輯與歌單播放介面。 |
| `/history` | `miku/views/home.py` | 觀看歷史紀錄頁面（存放於 Redis，依訪客 Cookie 關聯）。 |
| `/preferences` | `miku/views/home.py` | 使用者個人偏好設定（主題、語言、播放器設定）。 |
| `/licenses` | `miku/views/home.py` | 開源軟體授權與第三方套件宣告。 |
| `/robots.txt` | `miku/views/home.py` | 動態網路爬蟲政策（`strict`, `relaxed`, `PLEASE_INDEX_EVERYTHING`）。 |

---

## 2. 媒體與資源代理路徑

| 路由路徑 | 處理模組 | 功能職責說明 |
| :--- | :--- | :--- |
| `/proxy/pic/s/<sig>/*` | `miku/media/proxy.py`, `image_cache.py` | 經 HMAC 簽章驗證之 Bilibili 圖片代理（`*.hdslb.com`），具備本機磁碟 LRU 快取、單飛合併請求與 WebP 轉換。 |
| `/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>` | `miku/dash/stream.py` | 經 HMAC 簽章驗證之有界 HTTP Range DASH 分段代理（單次上限 32 MiB，無界請求拒絕 416）。 |
| `/proxy/live_hls` | `miku/media/proxy.py` | 經 HMAC 簽章驗證之伺服器端直播 HLS 播放清單與分段代理（自動重寫子 URI）。 |
| `/proxy/video/*` | `miku/media/proxy.py` | 針對缺失 DASH 節點的老舊投稿提供傳統漸進式 MP4 代理。 |
| `/proxy/download/<vid>/<idx>/<qual>` | `miku/dash/download_job.py` | 直接媒體下載端點（適用於無 JavaScript 環境，支援 `mode=video|audio|video_only` 與 `v_cid`、`a_qn`、`a_cid`）。 |
| `/proxy/live/disconnect` (POST) | `miku/views/live.py` | 優雅關閉伺服器上游活躍的直播連線。 |
| `/res/danmaku/<vid>[:<idx>]` | `miku/media/res.py`, `danmaku.py` | 抓取、Protobuf 解碼並輸出彈幕 XML（HMAC 簽章保護）。 |
| `/res/subtitle/<vid>[:<idx>[:<lan>]]` | `miku/media/res.py` | 取得並格式化字幕軌道（HMAC 簽章保護）。 |

---

## 3. 背景下載與組件 REST API

| 端點路徑 | 請求方法 | 功能職責說明 |
| :--- | :--- | :--- |
| `/download` | POST | 建立背景下載任務（支援 `mode`：`video`、`audio`、`video_only` 及選填 `v_cid`、`a_qn`、`a_cid`）→ 回傳 `{"job_id": "<uuid>"}`（獨立 `/audio/*` 曲目則回傳 `{"direct_url": "..."}`）。 |
| `/download/status/<job>` | GET | 輪詢查詢下載進度、速度與階段（`queued`, `resolving`, `downloading`, `muxing`, `ready`）。 |
| `/download/file/<job>` | GET | 串流傳輸封裝完成且具備 faststart 的 `.mp4`、`.mkv`、`.m4a` 或 `.flac` 檔案。 |
| `/download/cancel/<job>` | POST | 中斷下載任務並清除伺服器暫存檔案。 |
| `/api/component/player/<vid>/<idx>` | GET | 播放器 HTML 片段組件。 |
| `/api/component/meta/<vid>/<idx>` | GET | 影片詳情中繼資料 HTML 片段。 |
| `/api/component/comments/<vid>/<idx>/more` | GET | 分頁載入影片留言評論。 |
| `/api/component/comments/<vid>/<rpid>` | GET | 載入特定留言的子樓層回覆。 |
| `/toggle_theme` | POST | 更新佈景主題偏好 Cookie。 |
| `/set_lang` | POST | 更新介面語言偏好 Cookie。 |
