# 快取

快取存放於 Redis，單位均為分鐘，`0` 表示停用該路由的快取。所有受快取的頁面均回傳 `X-Cache: HIT／MISS` 標頭；未回傳該標頭，表示請求略過快取或該路由的快取已停用。

| Key | 環境變數 | 預設值 | 適用路由 |
| :--- | :--- | :--- | :--- |
| `space_minutes` | `SPACE_CACHE_MINUTES` | 5 | `/space/<mid>` 第一頁 |
| `space_json_minutes` | `SPACE_JSON_CACHE_MINUTES` | 5 | `/space/<mid>/json` |
| `video_minutes` | `VIDEO_CACHE_MINUTES` | 15 | `/video/<vid>`（資訊／標籤／相關推薦／分集；留言永遠即時） |
| `bangumi_minutes` | `BANGUMI_CACHE_MINUTES` | 60 | `/bangumi/view/<ssid>` |
| `author_minutes` | `AUTHOR_CACHE_MINUTES` | 30 | `/author/<mid>` |
| `article_minutes` | `ARTICLE_CACHE_MINUTES` | 30 | `/read/<cid>` 與 `/opus/<cid>` |
| `audio_minutes` | `AUDIO_CACHE_MINUTES` | 30 | `/audio/<auid>` 與 `/audio_list/<amid>` |
| `home_minutes` | `HOME_CACHE_MINUTES` | 30 | 首頁動態 |

## 核心設計

1. **空間頁面與 JSON 共用同一份上游資料**（`space:data:<mid>`）：單次 Bilibili 請求可同時服務兩個路由。該鍵值的逾期時間取兩邊 TTL 的較長者，但各路由讀取時仍各自檢查本身的 max-age，因此 TTL 設定不同仍各自嚴格生效。
2. **只快取健康資料**：異常回應與空資料（例如風險控制下回傳的「0 投稿」）一律不寫入快取，直接以即時回應提供。錯誤回應永不進入快取，因此快取不會把故障狀態固定下來。

深層頁面（`?i=N`，N＞1）各自擁有獨立的分頁鍵值（`space:data:<mid>:<pn>`）。`?listen=1` 與 `?format=` 匯出請求略過快取。
