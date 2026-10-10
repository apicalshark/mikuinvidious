# 站長指南 — 總覽

本指南適用於部署與管理 MikuInvidious 實例的人員。部署流程並不複雜，但建議先建立以下基本概念。

## 服務組成

`compose.yml` 定義三個服務（另有可選的 WARP 服務）：

| 服務 | 職責 | 對外連接埠 |
| :--- | :--- | :--- |
| `app` | 以 Granian 執行 Quart 主程式，所有應用邏輯均位於此服務 | 8080（內部網路） |
| `caddy` | 反向代理與靜態檔案伺服器，為使用者實際連接的入口 | 8000 |
| `redis` | 快取 API 回應並管理工作階段（session），為必要元件 | — |
| `warp`（可選） | 提供 Cloudflare WARP 的 SOCKS5 出口，供資料中心 IP 繞過風險控制使用 | 1080 |

請求路徑：使用者 → Caddy（8000）→ Granian（8080）→ Bilibili（直接連接，或經由 WARP 轉發）。

## 建議閱讀順序

1. [使用 Docker 快速啟動](quickstart-docker.md)——先完成部署並確認服務正常運作。
2. [組態參照](configuration.md)——查閱完整組態選項。
3. [反向代理](reverse-proxy.md)——綁定網域名稱並啟用 HTTPS。
4. [Bilibili 憑證](credentials.md)——評估是否需要登入憑證及其設定方式。
5. 其餘章節供日常查閱：[快取](caching.md)、[速率限制](rate-limiting.md)、[日常維運](maintenance.md)、[故障排除](troubleshooting.md)。
