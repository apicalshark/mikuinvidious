# Bilibili 連結與搜尋列

MikuInvidious 的搜尋列是一個具備智慧派送功能的通用輸入端。除了執行標準關鍵字搜尋外，它能自動識別 Bilibili 官方網址、剝離使用者追蹤參數，並將各類專屬代號直接導向目標頁面。

---

## 1. 貼入 Bilibili 分享連結與短網址

當您在 Bilibili 官方網頁或 App 點選「分享」時，所產生的連結通常為 `b23.tv` 短網址：

```text
https://b23.tv/AbCdEf
```

> [!WARNING]
> 官方 `b23.tv` 連結內嵌了大量的監視與追蹤代號（如 `share_source`、`share_medium`、`bbid`、`ts` 等），Bilibili 藉此關聯分享者與點擊者的身分畫像。

當您將任何 `b23.tv` 連結貼入 MikuInvidious 搜尋列時：
1. **伺服器端後端解析**：由 MikuInvidious 伺服器向上游發起 HTTP 重導向解析，絕不洩漏您的客戶端連線。
2. **清除所有追蹤標籤**：系統自動過濾並移除 `spm_id_from`、`from_source`、`share_*`、`mid` 等所有監控參數。
3. **瞬間無痕跳轉**：頁面立即安全重導向至純淨的資源目標（例如 `/video/BV1xx411c7mD`）。

---

## 2. 支援解析的官方網址格式

您可以直接將下列任何格式的 Bilibili 網址貼入搜尋列：

| 資源類型 | Bilibili 官方網址範例 | MikuInvidious 目標路徑 |
| :--- | :--- | :--- |
| **影片（BV 號）** | `https://www.bilibili.com/video/BV1xx411c7mD` | `/video/BV1xx411c7mD` |
| **影片（av 號）** | `https://www.bilibili.com/video/av170001` | `/video/av170001` |
| **指定分 P 影片** | `https://www.bilibili.com/video/BV1xx411c7mD?p=3` | `/video/BV1xx411c7mD?p=3` |
| **番劇單集（ep）** | `https://www.bilibili.com/bangumi/play/ep312456` | `/bangumi/play/ep312456` |
| **番劇季度（ss）** | `https://www.bilibili.com/bangumi/play/ss28241` | `/bangumi/view/28241` |
| **個人空間（UID）** | `https://space.bilibili.com/1234567` | `/space/1234567` |
| **直播間** | `https://live.bilibili.com/545068` | `/live/545068` |
| **專欄文章（cv）** | `https://www.bilibili.com/read/cv1234567` | `/read/1234567` |
| **動態 Opus** | `https://www.bilibili.com/opus/1234567890` | `/opus/1234567890` |
| **音訊單曲（au）** | `https://www.bilibili.com/audio/au123456` | `/audio/123456` |
| **音訊歌單（am）** | `https://www.bilibili.com/audio/am123456` | `/audio_list/123456` |

---

## 3. 獨立代號快速查詢

您完全不需要複製完整網址。只要在搜尋列輸入或貼入純代號，系統即可自動識別：

- `BV1xx411c7mD` 或 `bv1xx411c7mD` → 直接開啟該影片。
- `av170001` 或 `AV170001` → 直接開啟該傳統 av 號影片。
- `ep312456` → 直接跳轉至該番劇單集。
- `ss28241` → 直接開啟該番劇季度首頁。
- `cv1234567` → 直接開啟專欄文章。
- `au123456` → 直接開啟單曲播放頁。

---

## 4. 關鍵字搜尋與篩選器

若輸入內容非網址或專屬識別碼，系統會將其視為一般關鍵字進行全站搜尋。

### 搜尋分類切換
您可以切換頁籤以鎖定不同維度的搜尋結果：
- **影片**：一般使用者投稿的影音內容。
- **番劇**：官方動畫、電影與電視劇。
- **使用者**：頻道創作者與 UP 主。
- **直播**：目前正在開播的直播間。

### 影片篩選維度
搜尋影片時，篩選下拉選單支援以下進階條件：
- **排序依據**：
  - *綜合排序*（預設關聯度）
  - *最多點擊*（播放次數由多到少）
  - *最新發布*（依上傳日期倒序）
  - *最多彈幕*
  - *最多收藏*
- **影片時長**：
  - *全部時長*
  - *10 分鐘以下*
  - *10 至 30 分鐘*
  - *30 至 60 分鐘*
  - *60 分鐘以上*
- **內容分區**：鎖定音樂、動畫、遊戲、科技、知識等特定主題分區。
