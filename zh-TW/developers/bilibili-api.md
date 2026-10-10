# Bilibili API 封裝

`python/api/` 是自有封裝，2026 年 9 月從已封存的 `bilibili-api-python` 遷移完成。對外符號統一從 `api/__init__.py` 匯出。

## 模組

| 模組 | 內容 |
| :--- | :--- |
| `client.py` | HTTP 客戶端：WBI 簽名、`bili_ticket`、自動 Cookie、重試機制 |
| `credential.py` | `Credential` 認證類別 |
| `exceptions.py` | `ArgsException`、`ResponseCodeException` |
| `video.py` | 影片（資訊／標籤／相關推薦／分集／cid／playurl／彈幕） |
| `user.py` | 使用者（資訊／投稿／專欄） |
| `search.py` | `search_by_type` 與列舉 |
| `comment.py` | `get_comments` 與列舉 |
| `live.py` | `LiveRoom`、`LiveDanmaku`、分區 |
| `bangumi.py` | 番劇（詮釋資料／選集／索引） |
| `audio.py` | `Audio`、`AudioList` |
| `article.py`、`opus.py` | 專欄／動態 |
| `homepage.py`、`video_zone.py` | 首頁／分區動態 |

## WBI 簽名

1. `GET /x/web-interface/nav` 拿 `wbi_img.img_url` 和 `sub_url`。
2. 取檔名相接，過 OE 置換表得到 32 字元 `mixin_key`。
3. 每個請求加 `wts`（unix time），參數排序加 urlencode 後接上 `mixin_key`，MD5 算出 `w_rid`。
4. 遇到 `-403` 就清掉 key 快取、重抓 nav、重簽。

## bili_ticket

1. 算 `HMAC-SHA256(key="XgwSnGZ1p", msg=f"ts{int(time.time())}")`。
2. `POST /bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket`（hexsign 加 `key_id=ec02`）。
3. 取 `data.ticket`，快取 3 天。

## 風險控制實務

- **搜尋、評論、`getInfoByRoom` 要 WBI 加瀏覽器 TLS**：這幾支用 curl_cffi Chrome 偽裝（`_wbi_get`），沒簽名的 httpx 請求會拿到 `-352`。
- **playurl 風控看 `code==0 + data.v_voucher`**：`Video._request_playurl` 會走迴避通道再試一次（`isGaiaAvoided=true`、`gaia_source=pre-load`、`try_look=1`、`dm_img_*` 指紋）；還過不了就退回 PGC。
- **匿名要有指紋才拿得到完整畫質**：`dm_img_*` WebGL 模板指紋、`web_location=1315873` 和 `x-bili-device-req-json` 標頭；沒指紋的模式（`dm_img_switch=0`）匿名最高只有 480p。`/nav` 掛掉時用靜態 WBI 備援金鑰。
- **UGC 明細端點會對 PGC 的 BV 回假 404**（被風控時 `wbi/view`、`pagelist` 回 `-404`，PGC 端點正常）：playurl 解析**絕對不能**讓 UGC cid 失敗去否決 PGC——已知 `cid`（從 season 查到的 `pgc_cid`）直接拿來用；拿不到 cid 就只帶 `ep_id` 去打 PGC。
- **部分非 WBI 端點（`/x/web-interface/view`、彈幕、playurl）在資料中心 IP 也會回 412**：這是 IP 層級的擋，不是簽名問題，要走 WARP。
- **空間參數要跟官方 bundle 一字不差**：`acc/info` 是 `{mid, token:"", platform:"web", web_location:1550101}`；`arc/search` 是 `{..., order_avoided:"true"`（字串，不是布林值）、`platform:"web"`、`web_location:333.1387`、`special_type:""`、`index:0}` 再加 `dm_img_*`（`RISK_USER_LOG` middleware 模式，KvSDK 沒開就是 `dm_img_switch:"0"`）；備援鍵值是 `orderby` 不是 `order`。
