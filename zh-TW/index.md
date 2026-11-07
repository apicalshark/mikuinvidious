# MikuInvidious 文件

[MikuInvidious](https://github.com/apicalshark/mikuinvidious) 是一套自由且開源的 Bilibili 網頁前端服務，設計靈感源自 [Invidious](https://invidious.io)。本系統提供極致輕量、無追蹤且保護隱私的瀏覽體驗——無需註冊帳號，無需安裝官方客戶端，亦不傳送任何數位指紋。

---

## 核心功能特色

- **高畫質影音播放**：支援動態 ISO/IEC 23009-1 DASH 自適應串流、上游鏡像節點自動容錯、彈幕疊加、字幕顯示與多分集（多 P）切換。
- **萬用智慧搜尋列**：直接貼入官方 `b23.tv` 分享短網址、網頁/App 連結或純專屬代號（`BV...`、`ep...`、`cv...`），系統自動去追蹤並瞬間無痕跳轉。
- **純聽模式（`?listen=1`）**：將任何影片轉換為純音訊串流，極致節省行動流量、電池耗損與背景分頁資源。
- **多軌背景下載**：多軌併發分段下載音訊與視訊軌道，並由伺服器端 `ffmpeg` 自動無損封裝為標準 MP4 檔案。
- **直播串流**：低延遲原生 FLV 與 HLS 串流轉發，內建心跳維持機制與即時聊天室彈幕。
- **番劇與動畫**：作品簡介、劇集導覽與選用之 Nyaa.si BT 種子離線下載整合。
- **專欄與動態閱讀**：將 Bilibili `cv` 專欄與 `opus` 動態重構為無腳本、圖片全代理的純淨閱讀版面。
- **預設隱私隔絕**：所有視訊、音訊與圖片皆經由後端代理轉發並享有磁碟快取，客戶端 IP 絕不與 Bilibili CDN 產生接觸。

---

## 文件指南導讀

| 指南專區 | 適用對象 | 涵蓋重點內容 |
| :--- | :--- | :--- |
| **[使用者指南](guide/)** | 一般訪客與觀眾 | 官方連結自動解析、搜尋列密技、網址路由參數、播放器快捷鍵與背景下載。 |
| **[站長指南](operators/)** | 實例管理與維運人員 | 使用 Docker 快速啟動、Caddy TLS 終結、Redis 配置、完整組態參照與 Anubis 防禦。 |
| **[開發者指南](developers/)** | 程式碼與架構貢獻者 | Python 3.15+ 系統架構、模組化 DASH 串流引擎、Bilibili API 簽名與測試驗證。 |
| **[公開實例列表](instances)** | 所有使用者 | 社群志工架設並維護之公開可用實例清單。 |

---

## 快速連結

- **開始使用**：開啟您的實例網址（本地 Docker 預設位址為 `http://localhost:8000`）。
- **部署實例**：請參閱[使用 Docker 快速啟動](operators/quickstart-docker)。
- **理解系統設計**：請參閱[系統架構與設計](developers/architecture)。
- **原始碼儲存庫**：[apicalshark/mikuinvidious](https://github.com/apicalshark/mikuinvidious)（GNU AGPL-3.0 授權）。
