# 快取架構

MikuInvidious 採用跨行程記憶體、Redis 與本機磁碟的多層次快取架構，兼顧次毫秒級的極速回應與避免觸發上游風控之要求。

---

## 1. 頁面快取（Redis）

標準 HTML 視圖與資料負載由 Redis 進行快取。有效存留時間（TTL）於 `config.toml` 之 `[cache]` 區塊中以分鐘為單位定義，設定為 `0` 即可完全關閉該路徑之快取。

| 快取路徑 | 預設 TTL | 環境變數 | 說明 |
| :--- | :--- | :--- | :--- |
| `/space/<mid>` | 5 分鐘 | `SPACE_CACHE_MINUTES` | 個人空間第 1 頁，與 JSON 摘要共用。 |
| `/space/<mid>/json` | 5 分鐘 | `SPACE_JSON_CACHE_MINUTES` | 規格化最新投稿 JSON 摘要，與空間頁共用。 |
| `/video/<vid>` | 15 分鐘 | `VIDEO_CACHE_MINUTES` | 影片詳情（留言區永保即時動態）。 |
| `/bangumi/view/<ssid>` | 60 分鐘 | `BANGUMI_CACHE_MINUTES` | 番劇季度中繼資料與劇集清單。 |
| `/author/<mid>` | 30 分鐘 | `AUTHOR_CACHE_MINUTES` | 創作者摘要資料。 |
| `/read/<cid>`, `/opus/<cid>` | 30 分鐘 | `ARTICLE_CACHE_MINUTES` | 專欄文章與動態解析結果。 |
| `/audio/<auid>`, `/audio_list/<amid>` | 30 分鐘 | `AUDIO_CACHE_MINUTES` | 音訊單曲與歌單中繼資料及串流連結。 |
| `/`（首頁） | 30 分鐘 | `HOME_CACHE_MINUTES` | 首頁熱門推薦動態。 |

命中快取的回應皆會帶有 `X-Cache: HIT` 標頭，未命中則標記 `X-Cache: MISS`。

### 核心設計原則
1. **空間資料共用單一請求**：個人空間頁面與 JSON 端點向上游共用單一請求結果（`space:data:<mid>`），在較長的 TTL 到期前兩者各自驗證讀取時效，大幅減輕上游 API 負擔。
2. **絕不快取異常狀態**：僅有健康完整的資料才會寫入快取。上游連線失敗、逾時或風控所導致的空白清單絕不寫入快取，避免將短暫故障固化。
3. **繞過規則**：帶有 `?listen=1` 或自訂格式匯出的請求將自動繞過頁面快取。

---

## 2. DASH 播放網址快取（雙層快取）

隨選視訊播放採用位於 `python/dash_fetch.py` 的雙層快取機制：
- **L1 行程記憶體 LRU 快取**：在應用行程記憶體中快取最多 64 筆串流清單（`_DASH_MEM_MAX = 64`，`_DASH_MEM_TTL = 30s`）。播放中連續發起的音訊與視訊分段直接自記憶體取用，完全跳過 Redis 查詢。
- **L2 Redis 快取**：規格化各節點鏡像網址，將播放清單快取於 `miku_dash_<vid>_<idx>`，預設 TTL 為 1800 秒。
- **快速失敗降級**：若 Redis 發生逾時或短暫中斷，DASH 引擎會立即平滑降級為即時拉取，並進入 30 秒 Redis 冷卻期（`_DASH_REDIS_COOLDOWN`），保障媒體播放流暢不中斷。

---

## 3. 圖片磁碟快取（本機 LRU）

由 `python/image_cache.py` 處理的圖片代理請求（`/proxy/pic/*`）直接存放於本機專屬目錄：
- **預設路徑**：`data/img_cache`（由環境變數 `IMAGE_CACHE_DIR` 指定，Docker 中映射至 `img_cache` 儲存卷）。
- **配額與自動修剪**：預設上限為 2048 MB（`IMAGE_CACHE_MAX_SIZE_MB`）。當磁碟用量超出此上限時，系統自動啟動 LRU 清理，將容量削減至上限的 80%。
- **保留週期**：圖片預設保留 7 天（`IMAGE_CACHE_TTL_DAYS`）。
- **邊緣效能**：在 Caddy 反向代理後端，已快取的圖片回應時間低於 1 毫秒。
