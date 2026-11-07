# 日常維運與維護

查閱即時日誌、版本更新與資料備份之完整指南。

---

## 1. 查閱即時日誌

即時檢視應用程式與反向代理日誌：

```bash
docker compose logs -f app      # Quart 應用程式與 DASH 串流日誌
docker compose logs -f caddy    # Caddy 存取與 TLS 憑證日誌
docker compose logs -f redis    # Redis 資料庫運作狀態
```

> [!TIP]
> Granian 逐筆請求日誌預設為關閉（`SERVER_ACCESS_LOG=false`）。開啟此選項會因大量的分段視訊抓取、圖片讀取與下載進度輪詢而產生巨量日誌。

---

## 2. 版本升級

拉取最新功能更新、錯誤修正與套件升級：

```bash
git pull
docker compose build --pull app
docker compose up -d
```

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:550537684deb05ae191b2d17b985c9d15a50da65cec1e3c546013bd6867d3288 -->

對於手動（非 Docker）安裝環境：
```bash
git pull
uv sync
npm install
npm run build:css     # 若前端樣式有異動
```

完成上述指令後，請透過程序管理工具或部署時使用的啟動指令重新啟動應用程式，讓執行中的程序使用新版本。

<!-- /coderabbit-ai-generated:v1 -->

---

## 3. 備份策略

**強烈建議備份**：
- **組態設定**：您的 `config.toml` 設定檔（或 `compose.yml` 中的自訂環境變數）。
- **TLS 憑證資料**：Caddy 的憑證儲存卷（`caddy_data`），可在遷移主機時避免觸發 Let's Encrypt 頻率限制。
- **主加密金鑰**：若透過 `tools/encrypt_secrets.py` 加密了憑證，請將 `SECRETS_MASTER_KEY` 妥善保存於密碼管理器中，與程式碼分開儲存。

**無需進行備份**：
- **Redis 資料庫**：Redis 僅存放快取、暫態 Ticket 與速率限制計數器，隨時可清空重建。
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:cc3c66213a57ca41ca29bf701d6a5a5f538e41de27637ef5579ee02de7cf53f0 -->

- **使用者工作階段**：Quart 使用每次伺服器啟動時產生的隨機金鑰，為客戶端工作階段 Cookie 簽章，但不加密其內容；重新啟動伺服器會使既有工作階段失效。

<!-- /coderabbit-ai-generated:v1 -->
- **圖片磁碟快取**：`img_cache` 目錄在需要時自動重新產生，並依照 LRU 演算法自動維護容量。
