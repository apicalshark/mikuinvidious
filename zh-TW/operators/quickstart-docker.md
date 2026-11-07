# 使用 Docker 快速啟動

官方推薦且最為便捷的 MikuInvidious 部署方式。Caddy、Granian、Redis 與磁碟快取儲存卷皆已開箱即用預先配置。

---

## 1. 事前準備

- Docker Engine（v24.0+）
- Docker Compose（v2.20+）
- 防火牆開放連接埠 `80`（TCP）、`443`（TCP/UDP）與 `8000`（選用本機測試）。

---

## 2. 快速部署

1. **複製程式碼庫**：
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **準備 Caddy 設定檔**：
   ```bash
   cp Caddyfile.example Caddyfile
   ```

3. **啟動容器叢集**：
   ```bash
   docker compose up -d
   ```

您的實例將立即於 `http://localhost:8000` 啟動就緒。

---

## 3. 設定自訂公網網域名稱

1. 開啟 `compose.yml`，在 `app` 服務的環境變數中設定您的公網網址：
   ```yaml
   environment:
     - SITE_NAME=我的 MikuInvidious 實例
     - SITE_URL=https://miku.example.com
   ```

2. 編輯 `Caddyfile`，將第一行的 `:8000` 替換為您的實際網域：
   ```text
   miku.example.com {
       root * /usr/share/caddy

       handle /static/* {
           file_server
       }
       handle /site.webmanifest {
           root * /usr/share/caddy/static
           header Content-Type "application/manifest+json"
           file_server
       }

       @notProxy not path /proxy/*
       encode @notProxy zstd gzip

       handle {
           reverse_proxy app:8080 {
               header_up X-Real-IP {remote_host}
           }
       }
   }
   ```

3. 重啟服務容器：
   ```bash
   docker compose up -d
   ```

Caddy 將自動自 Let's Encrypt 或 ZeroSSL 申請並定期自動展延 HTTPS 憑證。

---

## 4. 資料中心 IP 提示

若主機架設於雲端機房（如 Hetzner、OVH、DigitalOcean、AWS），Bilibili 可能以 HTTP 412 或 `-352` 進行風控阻擋。請透過隨附的 `warp` 服務進行連線，或於 `compose.yml` 中設定 `HTTP_PROXY` / `HTTPS_PROXY` 指向家寬或 SOCKS5 出口。
