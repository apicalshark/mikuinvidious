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

對於手動（非 Docker）安裝環境：

```bash
git pull
uv sync
npm install
npm run build:css     # 若前端樣式有異動
```

完成上述指令後，請透過程序管理工具或部署時使用的啟動指令重新啟動應用程式，讓執行中的程序使用新版本。

---

## 3. 備份策略

**強烈建議備份**：

- **組態設定**：您的 `config.toml` 設定檔（或 `compose.yml` 中的自訂環境變數）。
- **TLS 憑證資料**：Caddy 的憑證儲存卷（`caddy_data`），可在遷移主機時避免觸發 Let's Encrypt 頻率限制。
- **主加密金鑰**：若透過 `tools/encrypt_secrets.py` 加密了憑證，請將 `SECRETS_MASTER_KEY` 妥善保存於密碼管理器中，與程式碼分開儲存。

**無需進行備份**：

- **Redis 資料庫**：Redis 僅存放快取、暫態 Ticket 與速率限制計數器，隨時可清空重建。

- **使用者工作階段**：Quart 為客戶端工作階段 Cookie 與 CSRF 權杖簽章，但預設不加密內容。未設定 `QUART_SECRET_KEY`（或 `[server].secret_key`）時，每次啟動會產生暫態金鑰並使重啟前的工作階段失效；正式環境建議設定固定 `QUART_SECRET_KEY` 以保留工作階段。

- **圖片磁碟快取**：`img_cache` 目錄在需要時自動重新產生，並依照 LRU 演算法自動維護容量。

---

## 4. 生產環境維運輔助腳本（`tools/`）

`tools/` 目錄內建多支對站長友善的腳本，協助快速初始化與維護公開站點：

| 腳本／目錄 | 用途說明 | 使用範例 |
| :--- | :--- | :--- |
| `tools/generate_secrets.py` | 產生 `QUART_SECRET_KEY`、`REDIS_PASSWORD` 及選用的 `SECRETS_MASTER_KEY`，並以 `0600` 權限寫入 `.env`（預設不覆寫既有金鑰，除非加上 `--force`）。 | `python3 tools/generate_secrets.py --include-master-key --write .env` |
| `tools/encrypt_secrets.py` | 使用 `SECRETS_MASTER_KEY` 加密 Bilibili 憑證（`SESSDATA`、`BILI_JCT`、`BUVID3`、`BUVID4`、`DEDEUSERID`），便於安全存放於 `config.toml` 或 `.env`。 | `uv run python tools/encrypt_secrets.py` |
| `tools/update_geoip.sh` | 下載並以原子替換（`mv -f`）更新 `GeoLite2-ASN.mmdb` 與 `GeoLite2-Country.mmdb`（來源為 `P3TERX/GeoLite.mmdb`），避免破壞 Anubis 的記憶體映射讀取，適合搭配 cron 定期執行。 | `./tools/update_geoip.sh ./data/geoip` |
| `tools/update_vps_asns.sh` | 抓取最新的 `vpsh.txt` 資料中心／VPS ASN 清單（自動排除 Cloudflare WARP ASN `13335` 與 `209242`），產出供 Anubis `botPolicy.yaml` 匯入的 `vpsh_asns.yaml`。 | `./tools/update_vps_asns.sh ./vpsh_asns.yaml` |
| `tools/warp/` | 供資料中心主機使用的獨立 Cloudflare WARP（`microwarp`）Docker Compose 範本，包含本機模式（`tools/warp/compose.yml.example`）與容器網路模式（`tools/warp/app/`）。 | `cp tools/warp/app/compose.yml.example tools/warp/app/compose.yml` |

