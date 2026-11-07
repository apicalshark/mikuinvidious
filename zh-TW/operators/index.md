# 站長指南

架設 MikuInvidious 實例所需之基礎設施極為精簡。官方建議採用 Docker Compose（`compose.yml`）進行部署，其協調以下微服務：

---

## 服務架構概覽

| 服務名稱 | 職責說明 | 連接埠 ／ 儲存卷 |
| :--- | :--- | :--- |
| `app` | 以 Granian ASGI 執行 Quart Python 3.15+ 應用程式。掛載 `img_cache:/app/data/img_cache`。 | `8080`（容器內部） |
| `caddy` | 反向代理與靜態資源伺服器（`file_server`）。對外公開的 HTTP/HTTPS 入口閘道。 | `8000`（HTTP）、`443`（HTTPS） |
| `redis` | 頁面快取、DASH 播放網址規格化、速率限制與時效憑證資料庫。*（使用者工作階段採用客戶端簽名 Cookie）。* | `6379`（容器內部） |
| `warp`（選用） | 提供 Cloudflare WARP 的 SOCKS5 代理出口（`1080`），供資料中心主機繞過 Bilibili 風險控制。 | `1080`（容器內部） |

---

## 請求資料流向

1. **入口流量**：瀏覽器連線至 Caddy（埠號 `:8000` 或 `:443`）。
2. **靜態資源**：Caddy 直接以 `file_server` 從磁碟傳遞 `/static/*`，附帶長效快取與 gzip/zstd 壓縮。
3. **應用程式與媒體代理**：Caddy 將動態請求轉送至 Granian（`:8080`），並注入客戶端真實 IP 標頭（`X-Real-IP`）。
4. **上游 API 與串流**：Granian/Quart 向上游 Bilibili 直連（家用寬頻環境）或經由 WARP 轉發（資料中心環境）。

---

## 站長建議閱讀路徑

1. [使用 Docker 快速啟動](quickstart-docker) —— 兩分鐘內完成容器化部署與驗證。
2. [組態參照](configuration) —— `config.toml` 與環境變數完整說明書。
3. [反向代理（Caddy）](reverse-proxy) —— TLS 憑證終結、靜態資源優化與 Anubis 機器人防禦。
4. [Bilibili 憑證](credentials) —— 透過加密儲存解鎖 1080P60、4K 與大會員付費番劇。
5. [快取](caching) —— Redis TTL 快取策略、雙層 DASH 快取與本機圖片磁碟儲存。
6. [速率限制](rate-limiting) —— 權杖桶滑動視窗防護與信任代理 CIDR 規範。
7. [日常維運](maintenance) 與 [故障排除](troubleshooting) —— 即時日誌、版本升級與風控應對。
