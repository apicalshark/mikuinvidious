# 路由與 API 參照

## 頁面路由（`views.py`、`views_bangumi.py`、`app.py`）

| 路由 | 說明 |
| :--- | :--- |
| `/` | 首頁動態（WBI `top/feed/rcmd`） |
| `/search` | 全站搜尋（影片／上傳者／專欄／直播／番劇分頁） |
| `/video/<vid>`、`/video/<vid>:<idx>` | 影片頁面與分集 |
| `/video_listen/<vid>[:<idx>]` | 純音訊模式 |
| `/video/dash/<vid>/<idx>/manifest.mpd` | DASH manifest（`?fresh=1` 強制重整） |
| `/live`、`/live/<room_id>` | 直播分區與房間 |
| `/live/chat/<room_id>` | 聊天室 SSE |
| `/space/<mid>`、`/space/<mid>/json` | 上傳者空間與 JSON feed |
| `/author/<mid>` | 作者頁面 |
| `/read/<cid>`、`/read/mobile/<cid>`、`/opus/<cid>` | 專欄／動態 |
| `/audio/<auid>`、`/audio_list/<amid>[:<idx>]` | 單曲／歌單 |
| `/bangumi`、`/bangumi/view/<ssid>`、`/bangumi/play/ep<id>` | 番劇索引／作品／單集 |
| `/bangumi/api/nyaa/<ssid>` | Nyaa 搜尋 API |
| `/history`、`/preferences`、`/licenses`、`/robots.txt` | 歷史紀錄／偏好設定／JS 授權／爬蟲政策 |
| `/<b32tvid>` | 短 ID 萬用入口 |
| `/vv/<zid>` | 分區動態 |

## 媒體／資源代理

| 路由 | 說明 |
| :--- | :--- |
| `/proxy/<subpath>` | 通用代理（圖片轉 WebP、progressive 影音） |
| `/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>` | DASH 軌道代理（Range 透傳） |
| `/proxy/download/<vid>/<idx>/<qual>` | 無 JS 環境的傳統下載 |
| `/proxy/live/disconnect`（POST） | 中斷直播轉發 |
| `/res/danmaku/<vid>[:<idx>]` | 彈幕 XML |
| `/res/subtitle/<vid>[:<idx>[:<lan>]]` | 字幕 |

## 下載任務／元件 API

| 路由 | 說明 |
| :--- | :--- |
| `/download`（POST） | 建立任務，回 `{"job_id"}` |
| `/download/status/<job>` | 輪詢進度 |
| `/download/file/<job>` | 下載成品 |
| `/download/cancel/<job>`（POST） | 取消任務 |
| `/api/component/player/<vid>/<idx>` | 播放器元件（含 `is_dash`／`dash_url`） |
| `/api/component/meta/<vid>/<idx>` | 資訊元件 |
| `/api/component/comments/<vid>/<idx>/more`、`/api/component/comments/<vid>/<rpid>` | 更多留言／留言串 |
| `/toggle_theme`、`/set_lang`（POST） | 主題／語言 cookie |

直播格式政策（FLV 優先、`LIVE_PREFER_HLS` 反轉）和「播放中不換格式」由伺服器端決定，模板用 `window.live_format` 告訴前端載 mpegts 還是 hls；VOD 頁面兩個都不載。
