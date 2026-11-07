# 速率限制與防爬蟲

MikuInvidious 內建基於 Redis 權杖桶的滑動視窗速率限制器，保護公開實例免於惡意爬蟲掃描與阻斷服務攻擊（DoS）。

速率限制**預設為關閉狀態**。對於直接暴露於公網之公開站點，強烈建議啟用此功能。

---

## 1. 啟用速率限制器

於 `config.toml` 中配置：

```toml
[rate_limit]
enabled = true
trusted_proxies = "172.18.0.0/16, 10.0.0.0/8"
```

或透過環境變數傳遞：

若使用 Docker 部署，請在 `compose.yml` 的 `app` 服務環境變數中設定：

```yaml
environment:
  - RATE_LIMIT_ENABLED=true
  - TRUSTED_PROXIES=172.18.0.0/16
```

若直接以 CLI 啟動伺服器，請在執行指令前先匯出環境變數：

```bash
export RATE_LIMIT_ENABLED=true
export TRUSTED_PROXIES="172.18.0.0/16"
uv run python/main.py
```

> [!IMPORTANT]
> `config.toml` 中的設定值優先權高於環境變數。若 `config.toml` 中含有 `[rate_limit] enabled = false`，將會覆蓋 `RATE_LIMIT_ENABLED=true`。若希望由環境變數控制，請自 `config.toml` 移除衝突的 `enabled` 設定項，或直接在 `config.toml` 中將其設為 `true`。

---

## 2. 信任代理與客戶端 IP 辨識

速率限制器透過 `python/rate_limit.py` 中的 `get_client_ip()` 判定客戶端 IP：

1. **直接對端檢查**：若連線對端非私有網路位址且未列於 `trusted_proxies` 中，系統直接採信 TCP 對端 IP。任何由未受信任對端發送之 `X-Real-IP` 或 `X-Forwarded-For` 將被完全忽視，防止訪客偽造身分。
2. **信任代理轉發**：若連線對端為本機 loopback（`127.0.0.1`、`::1`）、私有網路網段（如 Docker 虛擬網路 `172.18.0.0/16`），或明確符合 `trusted_proxies` 中的 CIDR 規則，應用程式採信 `X-Real-IP` 與 `X-Forwarded-For`。

> [!WARNING]
> **私有網路預設信任注意事項**：由於 loopback 與 RFC 1918 私有網路預設受採信，請務必將應用程式所配置之監聽埠號（如裸機部署的 8888 或容器網路的 8080）透過防火牆阻擋外部直接存取，並確認您的反向代理（Caddy）在轉送時確實將 `X-Real-IP` 覆寫為驗證過的客戶端遠端 IP。

---

## 3. 預設速率限制等級

系統依據端點敏感程度配置專屬配額：

| 等級 | 配額上限 | 時間視窗 | 適用端點範圍 |
| :--- | :--- | :--- | :--- |
| **Strict** | 10 次 | 60 秒 | 敏感管理操作與憑證設定相關端點。 |
| **Search** | 20 次 | 60 秒 | 分類與關鍵字搜尋端點（`/search`）。 |
| **Normal** | 60 次 | 60 秒 | 一般影片詳情頁面、個人空間與番劇動漫。 |
| **Proxy** | 100 次 | 60 秒 | 媒體串流分段與圖片代理。 |
| **Loose** | 300 次 | 60 秒 | 輔助靜態資源。 |

當客戶端超出配額時，伺服器回傳 `HTTP 429 Too Many Requests` 與 `Retry-After` 標頭。正常回應亦會附帶標準監控標頭：

- `X-RateLimit-Limit`：該視窗內允許之最大請求量。
- `X-RateLimit-Remaining`：當前視窗剩餘額度。
- `X-RateLimit-Reset`：目前計數視窗重設之 UNIX 時間戳記。
