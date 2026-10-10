# 播放與下載架構

DASH 是正式播放路徑，progressive `durl` 只留給沒有 DASH 軌道的上稿做備援。

## DASH 鏈路

1. **取得 playurl**（`api/video.py:get_dash_playurl`、`dash_proxy.py:video_get_dash_for_qn`）：UGC 打 WBI playurl（`fnval=4048, fourk=1, qn=120`），PGC 退回 `/pgc/player/web/v2/playurl`。結果快取在 Redis（`miku_dash_<vid>_<idx>`，1800 秒）。
2. **產生 MPD**（`generate_vod_mpd`）：位於 `/video/dash/<vid>/<idx>/manifest.mpd`，只收有合法 `SegmentBase.indexRange` 的軌道。BaseURL 指向軌道代理，每次請求即時解析，不預烘進 manifest。
3. **軌道代理**（`/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>`）：把客戶端的 `Range` 標頭原樣轉發，回 `206` 和 `Content-Range`／`Content-Length`／`Accept-Ranges`。dash.js 靠 sidx 自己切段，**只有回得出長度的 206 才算有效**——算不出長度的直接跳過換 mirror，不然 dash.js 會卡在 "non-computable download size"（dash.js#4716）。前端 `fragmentRequestTimeout` 60 秒，abandon 規則開著。
4. **容錯策略**：健康節點優先，加上跨請求的異常節點記憶（只記傳輸失敗，403／412／514 不記；冷卻 120 秒，成功馬上清除）。每個 fragment 最多試 2 個節點、握手 4 秒（`DASH_PLAYBACK_ATTEMPT_TIMEOUT`）——dash.js 自己大概 7 秒就放棄，再深的遍歷只會讓客戶端先超時，不如快點失敗讓它重試或降畫質。握手完成後如果斷線或 5 秒沒收到位元組，就對下一個 mirror 續傳剩下的 `Range`（位元組連續，dash.js 無感）；單純速度慢不換節點（備援 URL 是留給故障用的，慢速由 dash.js 的 ABR 處理）。
5. **簽名過期才重拿**：CDN 網址是時效簽名（有效期約兩小時，認 `expires|wsTime|txTime|um_deadline|deadline` 五種參數寫法）。只有 403 級別掃蕩或戳記確認過期才重拿 playurl 並重試（相同戳記去重）；`?fresh=1` 強制重整，給播放器自救。**速度慢不重拿**——新簽名指到同一台爛機器也沒用。

## 前端

`static/vjs/dash.min.js` 是內嵌的 v5.2.1（用 `npm run sync:static` 從 `dashjs` 套件同步）。**播放器程式只能用 v5 representations API**，v4 的 `getBitrateInfoListFor`／`setQualityFor` 已經移除。`DashPlayerManager` 在 `static/themes/modern/js/player.js`。

## durl-only 備援

有些上稿不管換哪種 endpoint 和參數組合，都只回 `durl` 而沒有 `dash` 節點（2026-09-11 驗證）。`has_valid_dash_tracks()` 負責把關：沒有 DASH 軌道就改走 `fetch_durl_supported_src()`（非 WBI playurl 加 PGC 重試，並行解析），快取為 `mikuinv_<vid>_<idx>_<qn>`（`+_bak` 放備援），走原生 `/proxy/video/...` 播放。PGC durl 每個 qn 只回一個節點，所以只留精確匹配（不然選單會出現同一個檔案掛四個名字的錯誤）。**progressive 抓 CDN 要用 Web Chrome UA**（`build_cdn_headers()`，BiliDroid UA 會被 upos／akamaized 邊緣節點回 403）。

## 下載任務

`POST /download` 開背景任務（最多 3 個並行，用 uuid 當 ID），回 `{"job_id"}`；`static/themes/modern/js/download.js` 的浮動窗輪詢 `GET /download/status/<job>`（進度、速度、階段：queued／resolving／downloading／muxing／ready），完成自動存 `GET /download/file/<job>`。取消走 `POST /download/cancel/<job>`（也支援 beacon），會斷掉 CDN 連線、殺掉 ffmpeg、清掉暫存檔。

下載跟播放是同一套 mirror／watchdog 機制，但**預算放得更寬**（握手 8 秒、整輪掃、每段 5 次 backoff 重試）：下載沒有客戶端智慧，只能靠伺服器端撐住。用 32MB Range 分段抓，影像和音軌並行（學 dash.js 的雙 adaptation set），1MB 批次寫檔；每段輪換起始節點（異常的往後排、M-CDN 放最後），加上自適應 pacing（連續太慢就照 2／4／6／8／10 秒階梯拉長間隔——per-IP 節流會累積也會消退，出現乾淨的快速段就歸零）；keep-alive 連線重用；current-speed slowdown watchdog（連三個 1 秒視窗低於 300KB/s、而且之前證明跑得到 ≥1MB/s，就斷線 10 秒後再連——立刻重連只會回到節流區間）。只有簽名失效（403 掃蕩或戳記過期）才重整 playurl，無次數上限並做相同戳記去重。

DASH 下載完用 `ffmpeg -c copy -movflags +faststart` 合成（app 映像檔內建 ffmpeg）；durl-only 本來就是單一 MP4，直接存檔。

## 實作約束

- DASH CDN 要 Web UA，Android App UA 直接回 403。
- `bili_ticket` Cookie 永遠不送給 Web API（只走 CDN 代理的 `x-bili-ticket` 標頭），不然會觸發 `v_voucher` 預檢查。
- **播放快速失敗、下載撐到底——這是設計決策，不是缺陷**。不要把兩條路「統一」。
