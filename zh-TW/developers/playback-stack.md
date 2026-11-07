# 播放與下載子系統規範

MikuInvidious 採用動態自適應 HTTP 串流（ISO/IEC 23009-1 DASH）作為其主要的隨選視訊管線，並保留傳統漸進式 MP4（`durl`）作為老舊投稿影片的自動備援。

---

## 1. 架構宏觀概覽

DASH 串流引擎在 `python/` 中拆分為六個解耦的模組：

```
               [ 瀏覽器客戶端 (dash.js v5) ]
                              |
            +-----------------+-----------------+
            |                                   |
            v /video/dash/.../manifest.mpd      v /proxy/dash/... (分段串流)
    +-----------------+                 +-----------------------+
    |   dash_mpd.py   |                 |    dash_stream.py     |
    | 動態 MPD 清單   |                 | HTTP Range 分段代理   |
    |                 |                 | Socket 連線複用池     |
    +-----------------+                 +-----------------------+
            |                                       |
            v                                       v
    +-----------------+                 +-----------------------+
    |  dash_fetch.py  |                 |    dash_common.py     |
    | 播放網址取得與  |                 | 鏡像健康狀態與快取    |
    | 雙層快取機制    |                 |                       |
    +-----------------+                 +-----------------------+
            |                                       |
            +-------------------+-------------------+
                                |
                                v
               [ 背景非同步下載佇列 ]
                                |
                +-------------------------------+
                |     dash_download_job.py      |
                |   併發控制與 REST 輪詢端點    |
                +-------------------------------+
                                |
                +-------------------------------+
                |     dash_download_track.py    |
                | 分段下載器與無損封裝          |
                +-------------------------------+
```

---

## 2. 核心子系統

### A. 播放網址取得與雙層快取（`dash_fetch.py`）

- **解析策略**：透過 WBI 簽名帶有 `fnval=4048, fourk=1, qn=120` 向上游請求 UGC 播放網址，番劇動漫則自動退避至 `/pgc/player/web/v2/playurl`。
- **雙層快取**：
  - **L1 行程記憶體**：以記憶體 LRU 快取（`_DASH_MEM_MAX = 64` 筆，`_DASH_MEM_TTL = 30s`）承接密集的視訊分段請求。命中時完全繞過 Redis，消除連續分段的查詢負擔。
  - **L2 Redis 快取**：快取於 `miku_dash_<vid>_<idx>`，預設 TTL 為 1800 秒。儲存鍵將 `base_url` 與 `backup_url` 完整規格化。
- **快速失敗保護**：Redis 發生逾時或斷線時自動平滑降級，不拋出例外中斷播放。系統具備 30 秒冷卻時間（`_DASH_REDIS_COOLDOWN`），避免後續分段反覆遭遇 Redis 逾時阻滯。時效過期或 403 失效的權杖將主動清除記憶體快取。

### B. 動態 MPD 生成（`dash_mpd.py`）

- 於 `/video/dash/<vid>/<idx>/manifest.mpd` 輸出符合 `isoff-on-demand` 規範的標準 DASH 清單。
- 僅收錄具備有效 `SegmentBase.indexRange` 標頭的音訊與視訊軌道。
- BaseURL 動態指向本機分段代理路徑（`/proxy/dash/...`），於每次請求時動態計算，不硬編碼於靜態清單中。

### C. 軌道代理與上游連線複用（`dash_stream.py`）

- **Range 標頭轉送**：透過原生 Socket `CdnConnection`（直連或經由 WARP SOCKS5）轉發客戶端的 `Range` 標頭，回傳 `206 Partial Content`。
- **有界範圍約束**：客戶端必須發送明確有界的 Range 請求（`bytes=<起點>-<終點>`），且最大長度不得超過 32 MiB（`DASH_MAX_FRAGMENT_BYTES = 32 * 1024 * 1024`）；無界請求（如 `bytes=0-`）或超出 32 MiB 者將直接以 `HTTP 416 Range Not Satisfiable` 拒絕。
- **可計算的 Content-Length**：`dash.js` 仰賴確定的大小計算傳輸速率與 ABR 自動畫質。回應缺失長度者將被捨棄或由 `Content-Range` 推導補全，防範播放器逾時中斷。
- **上游 Socket 複用（`_PlaybackKeepAlivePool`）**：依照 `(proxy, url)` 快取完全讀取完畢且長度明確的連線。連續分段直接複用已完成 TCP+TLS 交握的現有連線，消除慢啟動懲罰。閒置連線於 30 秒後自動回收（連線池容量上限為 8）。
- **持久化 TLS 上下文**：全域複用 `stream.py` 中的 `_TLS_CONTEXT`，避免每次建立連線時反覆載入解析 CA 憑證鏈。

### D. 鏡像容錯與邊緣健康狀態記憶（`dash_common.py`）

- **鏡像挑選**：候選節點依照健康程度排序。傳輸失敗會記錄 120 秒病態冷卻期，請求成功則立即解除標記。
- **Range 敏感節點辨識**：在檔案中間回傳 `416 Range Not Satisfiable` 的薄型 CDN 邊緣節點將依據偏移量標記為 Range 敏感節點，避免後續分段再度發送至該故障節點。
- **掛鐘時間掃描預算**：嘗試鏡像受到嘗試次數與時間雙重限制（`DASH_PLAYBACK_MIN_MIRRORS = 2`，`DASH_PLAYBACK_MIRROR_BUDGET = 3s`，`DASH_PLAYBACK_ATTEMPT_TIMEOUT = 4s`）。3 秒預算用於限制在嘗試前兩個鏡像後是否繼續嘗試更多備援節點。快速斷線能迅速切換備援，而慢速卡死邊緣則在播放器逾時前果斷切換。
- **串流中途容錯**：串流若遭遇停滯且無資料時間超過 `DASH_BODY_IDLE_TIMEOUT`（5 秒），系統自動在下一個健康鏡像無縫接續剩餘的位元組區段。

### E. 背景下載管線（`dash_download_*.py`）

- **佇列與併發**：由 `dash_download_job.py` 排程（上限為 3 個併發下載任務），每個任務獲派一組不可推測的 UUID。
- **分段下載**：以 32 MB 為單元並行下載音訊與視訊軌道，並以 1 MB 進行本機磁碟批次寫入。
- **自適應降速避障與看門狗**：連續遭遇傳輸慢速將觸發自適應退避階梯（2 至 10 秒）。1 秒滾動看門狗偵測到 CDN 節流時，自動中斷連線並暫停後重試。
- **格式封裝與交付**：下載完畢後呼叫 `ffmpeg -c copy -movflags +faststart` 無損合成為可直接隨選預覽的 MP4 檔案。

---

## 3. 前端播放器

- **VOD 播放器（`dash.js`）**：內嵌 v5 representations API（`static/themes/modern/js/player.js`）。
- **直播串流播放器**：`hls.js`（v1.7.4，處理 HLS fmp4 master）與 `mpegts.js`（處理 FLV 串流）。直播播放腳本嚴格隔離，絕不於隨選視訊頁面載入。

---

## 4. 關鍵不變原則

- **DASH CDN 使用者代理**：必須發送桌面 Chrome UA（`python/fingerprints.py` 中的 `build_cdn_headers()`）。發送 Android UA（`BiliDroid`）將直接引發 `.bilivideo.com` 403 存取拒絕。
- **Ticket Cookie 與 Header 規範**：絕不能將 `bili_ticket` 當作 Cookie 傳送給 Web API（這會誘發防爬蟲 `v_voucher` 檢查）；它只能以 `x-bili-ticket` 標頭形式發送給 CDN 代理。
- **播放與下載邏輯嚴格分離**：
  - *播放路徑*：快速失敗（至少 2 個鏡像，4 秒交握，3 秒時間預算）。由前端 dash.js 自行調配 ABR 畫質。複用上游連線。
  - *下載路徑*：持續堅持（自適應退避階梯，1 秒降速看門狗，10 秒冷卻，32 MB 續傳）。
  - *絕不可將兩套迴圈合併。*
