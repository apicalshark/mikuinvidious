# Bilibili 上游 API 參照與簽名規範

本文件記載本機客戶端模組（`python/api/`）中所實作的 Bilibili API 端點、查詢參數與加密簽名架構。

---

## 1. 上游 API 端點

### 影片（`api/video.py`）

- `GET https://api.bilibili.com/x/web-interface/view` —— 基本影片中繼資料（aid/bvid）。
- `GET https://api.bilibili.com/x/web-interface/view/detail/tag` —— 影片標籤。
- `GET https://api.bilibili.com/x/web-interface/archive/related` —— 相關影片推薦。
- `GET https://api.bilibili.com/x/player/pagelist` —— 多 P 分集清單。
- `GET https://api.bilibili.com/x/player/wbi/playurl` —— **UGC DASH/Durl 播放連結（經 WBI 簽名）。** 必須附帶 `web_location=1315873`、`fnval=4048`、`fourk=1` 與 `x-bili-device-req-json` 標頭。
- `GET https://api.bilibili.com/x/player/wbi/v2` —— 字幕與彈幕設定（經 WBI 簽名）。

### PGC 與番劇動畫（`api/bangumi.py`）

- `GET https://api.bilibili.com/pgc/view/web/season` —— 季番詳情、單集索引與作品名稱。
- `GET https://api.bilibili.com/pgc/player/web/v2/playurl` —— **PGC 官方播放連結。** 接收 `ep_id` 或 `cid`。
- `GET https://api.bilibili.com/pgc/web/season/section` —— 預告片與特別花絮。
- `GET https://api.bilibili.com/pgc/season/index/result` —— 番劇分類檢索清單。

### 個人空間與使用者（`api/user.py`）

- `GET https://api.bilibili.com/x/space/wbi/acc/info` —— 使用者個人資訊（經 WBI 簽名）。
  - *風控退避機制：* 遭遇 412 或 `-352` 時自動退避呼叫 `GET https://api.bilibili.com/x/web-interface/card`。
- `GET https://api.bilibili.com/x/space/wbi/arc/search` —— 影片投稿列表（經 WBI 簽名，附帶 `order_avoided="true"`）。
  - *風控退避機制：* 退避呼叫 `GET https://api.bilibili.com/x/series/recArchivesByKeywords` 並附帶 `dm_img_*` 數位指紋。
- `GET https://api.bilibili.com/x/space/wbi/article` —— 使用者專欄文章列表（經 WBI 簽名）。

### 直播串流（`api/live.py`, `api/live_area.py`）

- `GET https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom` —— 直播間詳情。必須經 WBI 簽名並帶有 `web_location=444.8`，且透過 `curl_cffi` 模擬瀏覽器 TLS 指紋。
- `GET https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo` —— 串流格式規格選擇（FLV、HLS、編碼）。
- `GET https://api.live.bilibili.com/xlive/web-room/v1/playUrl/playUrl` —— 直播主串流網址。
- `GET https://api.live.bilibili.com/xlive/web-interface/v1/second/getList` —— 子分區分類標籤。

### 搜尋與討論（`api/search.py`, `api/comment.py`）

- `GET https://api.bilibili.com/x/web-interface/wbi/search/type` —— 分類搜尋（經 WBI 簽名）。
- `GET https://api.bilibili.com/x/v2/reply/main` —— 影片與專欄評論留言討論串。

### 音訊、專欄與首頁動態（`api/audio.py`, `api/article.py`, `api/opus.py`, `api/homepage.py`, `api/video_zone.py`）

- `GET https://www.bilibili.com/audio/music-service-c/web/song/info` —— 單曲資訊。
- `GET https://www.bilibili.com/audio/music-service-c/web/url` —— 音訊串流播放網址。
- `GET https://www.bilibili.com/audio/music-service-c/web/menu/info` —— 歌單資訊。
- `GET https://www.bilibili.com/audio/music-service-c/web/song/of-menu` —— 歌單內收錄之歌曲。
- `GET https://api.bilibili.com/x/article/viewinfo` —— 專欄文章附加統計（讚數、硬幣、收藏與標題備援）。文章主體由 `views_space.py` 直接自網頁 HTML 解析。
- `GET https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail` —— 動態圖文內容。
- `GET https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd` —— 首頁熱門推薦動態（經 WBI 簽名）。
- `GET https://api.bilibili.com/x/web-interface/dynamic/region` —— 分區動態串。

---

## 2. 簽名雜湊機制與安全憑證

### WBI 查詢字串簽名（`api/client.py`）

1. **取得金鑰**：向 `/x/web-interface/nav` 取得 `wbi_img.img_url` 與 `wbi_img.sub_url`。
2. **混合金鑰衍生**：串接檔名基底並透過 Bilibili OE 查表置換，計算出 32 字元的 `mixin_key`。
3. **查詢雜湊簽章**：
   - 若 `web_location` 為空則預設賦予 `1315873`。
   - 附加 `wts=<unix_timestamp>`。
   - 依照鍵名按字母順序嚴格遞增排序。
   - 使用百分比編碼對鍵與值進行 URL 編碼，空白字元必須編碼為 `%20`（絕不可編碼為 `+`，否則簽章驗證失敗）。
   - 以 `&` 串接鍵值對，末尾附加 `mixin_key`。
   - 計算 `MD5` 雜湊摘要產生 `w_rid`。
4. **失效處理**：若上游 JSON 回應主體含有 `"code": -403`，立即清除快取之金鑰並重新向 `/x/web-interface/nav` 獲取後重新簽章重試。

### `bili_ticket` 票券生成

1. 計算 `HMAC-SHA256(key="XgwSnGZ1p", message=f"ts{int(time.time())}")`。
2. 向上游 `POST https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket` 發送：
   - `key_id: "ec02"`
   - `hexsign: <十六進位簽名>`
   - `context[ts]: "<時間戳記>"`
   - `csrf: ""`
3. 取得 `data.ticket` 並快取至多 3 天。
4. **標頭規範**：只能透過 `x-bili-ticket` HTTP 請求標頭發送至 CDN 代理；絕不可作為瀏覽器 Cookie 發送（這會觸發反爬蟲空結果）。

### Gaia 風控規避（`api/video.py`, `api/user.py`）

- **播放網址避障通道**：當播放網址回傳 `v_voucher` 驗證碼或 `-352` 時，`api/video.py` 自動帶入 WebGL/Canvas 虛擬指紋（`dm_img_*`）與 `gaia_source=pre-load`、`isGaiaAvoided=true`、`try_look=1` 發起避障重試。
- **個人空間退避**：`/x/space/wbi/acc/info` 異常時自動退避至 `/x/web-interface/card`；投稿搜尋失敗時退避至 `/x/series/recArchivesByKeywords`。
- **番劇偽 404 退避**：風控可能導致 UGC 介面將番劇 BV 回傳 `-404`，系統嚴格禁止讓 UGC CID 失敗否決 PGC 播放流程，自動由 `ep_id` 退避路徑接續。
