# 反向代理（Caddy）

Caddy 是 MikuInvidious 的公網入口閘道：它負責自動申請與展延 TLS 憑證、直接以高效能檔案伺服器傳送靜態資源、壓縮動態文字回應，並將應用程式請求代理至 Granian（`app:8080`）。

---

## 1. 標準正式環境 Caddyfile 設定

```text
{
    # 停用 HTTP/3 (QUIC)，防範 206 Partial Content 串流發生 ERR_QUIC_PROTOCOL_ERROR
    servers {
        protocols h1 h2
    }
}

:8000 {
    # 設定靜態資源網站根目錄
    root * /usr/share/caddy

    log {
        output stdout
        format json
    }

    request_body {
        max_size 1MB
    }

    header -Server

    # 直接自磁碟提供靜態資源（完全不經過 Python 應用程式）
    handle /static/* {
        header X-Content-Type-Options "nosniff"
        header X-Frame-Options "DENY"
        header Referrer-Policy "strict-origin-when-cross-origin"
        header Permissions-Policy "camera=(), microphone=(), geolocation=()"
        header Cross-Origin-Resource-Policy "same-origin"
        file_server
    }

    handle /site.webmanifest {
        root * /usr/share/caddy/static
        header Content-Type "application/manifest+json"
        header X-Content-Type-Options "nosniff"
        header Cross-Origin-Resource-Policy "same-origin"
        file_server
    }

    # 壓縮 HTML、JSON 與文字內容（嚴禁對媒體串流重複壓縮）
    @notProxy not path /proxy/*
    encode @notProxy zstd gzip

    # 將其餘所有動態請求轉發給 Python 應用程式
    handle {
        reverse_proxy app:8080 {
            header_up X-Real-IP {client_ip}
            header_up X-Forwarded-For {client_ip}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
```

---

## 2. 關鍵組態注意事項

### 必須停用 HTTP/3（QUIC）

現代瀏覽器的 QUIC 實作在串流分段媒體（`206 Partial Content`）時極易觸發 `ERR_QUIC_PROTOCOL_ERROR`。請務必在全域設定中加入：

```text
{
    servers {
        protocols h1 h2
    }
}
```

### 排除媒體串流之重複壓縮（`@notProxy`）

媒體串流（`/proxy/*`）本身已是高度壓縮的視訊與音訊編碼。若再次壓縮將浪費伺服器 CPU 運算並引發延遲阻滯。請務必使用 `@notProxy not path /proxy/*` 限制 `encode` 作用範圍。

### 客戶端真實 IP 轉發

Granian 位於 Caddy 後方。為確保速率限制器與日誌能取得訪客的真實公網 IP 而非 Docker 內部橋接 IP，Caddy 必須設定傳遞解析出的客戶端 IP 與協定：

```text
reverse_proxy app:8080 {
    header_up X-Real-IP {client_ip}
    header_up X-Forwarded-For {client_ip}
    header_up X-Forwarded-Proto {scheme}
}
```

> [!WARNING]
> 若 Caddy 前方另有上游 CDN 或負載平衡器（如 Cloudflare 或 AWS ALB），切勿直接使用 `{remote_host}`。因為在此架構下，`{remote_host}` 所取得的將是上游 CDN 節點的 IP 而非實際訪客 IP，會導致所有外部訪客共用同一個速率限制配額。
>
> 正確的做法是在 Caddy 的全域 `servers` 區段設定 `trusted_proxies` 僅信任已知之上游代理 CIDR 網段，並指定解析來源標頭：
>
> ```text
> servers {
>     protocols h1 h2
>     trusted_proxies static <上游代理_CIDR_網段>
>     client_ip_headers CF-Connecting-IP X-Forwarded-For
> }
> ```
>
> Caddy 將確認連線來自受信任的上游 CIDR 後，安全地自標頭提取真實訪客 IP 填入 `{client_ip}`，防範偽造標頭並保留 `{scheme}` 通訊協定。

---

## 3. 搭配 Anubis PoW 進行高流量機器人防禦

針對遭受大量爬蟲抓取或分散式流量攻擊的公開站點，MikuInvidious 在 `example/docker/` 目錄中提供生產環境專用的 Anubis 工作量證明（Proof-of-Work, PoW）防護範本（`compose.yml.example`、`Caddyfile.prod.example` 與 `botPolicy.yaml.example`）。

```
[ 瀏覽器 ／ 爬蟲客戶端 ]
         |
         v
    [ Caddy ]  （TLS、安全性標頭、排除 /proxy/* 壓縮）
         | （/static/* 與 /site.webmanifest 繞過 Anubis 直連 app:8080）
         v
    [ Anubis PoW 閘道 ] -- 針對可疑爬蟲與無頭腳本發起 PoW 算力驗證（:8923）
         |
         v （通過驗證的真實瀏覽器）
    [ Granian / Quart ]  --> [ Redis ] / [ microwarp SOCKS5 ]
```

- **靜態資源繞過**：`/static/*` 與 `/site.webmanifest` 繞過 Anubis 直接轉發至 `app:8080`，並附加 `Permissions-Policy`、`Cross-Origin-Resource-Policy: same-origin` 與 `Cache-Control: public, max-age=86400`。
- **全站安全性標頭**：`Caddyfile.prod.example` 會移除 `Server`／`Via`，並在正常回應與 `handle_errors` 錯誤回應中統一套用 `Strict-Transport-Security`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY` 與 `Referrer-Policy`。
- **圖片代理防護（`/proxy/pic/*`）**：由 Anubis PoW 挑戰保護；在通過挑戰後，由 Python 直接自本機磁碟以 `< 1ms` 極速回應。
- **針對性防禦規則（`botPolicy.yaml.example`）**：阻擋不支援的 HTTP 方法、漏洞掃描路徑（`.env`、`.git`、`.php`）與惡意 AI／SEO 爬蟲；對腳本函式庫與無頭瀏覽器框架發起 PoW 挑戰；針對資料中心／VPN ASN（`+15`）與特定 GeoIP 區域（`+10`）加權計分；對來自資料中心或目標區域的 `/space/*/json` 請求要求 PoW 驗證，同時放行一般住宅網路的 RSS／腳本存取；於 `load_1m >= 2.0` 時啟動高負載降載，並依風險等級套用 `4` 至 `10` 的階梯式 PoW 難度。

---

## 4. 使用 `example/docker/` 部署公開實例

`example/docker/` 目錄提供完整的強化版 Docker Compose 公開實例範本，整合了 Caddy、Anubis、Redis 與 `microwarp`（Cloudflare WARP SOCKS5 出口）。

### 部署步驟

1. **複製範例設定檔**至部署目錄：

   ```bash
   cp example/docker/compose.yml.example compose.yml
   cp example/docker/Caddyfile.prod.example Caddyfile
   cp example/docker/botPolicy.yaml.example botPolicy.yaml
   ```

2. **準備 GeoIP 資料庫**（`botPolicy.yaml` 中的 `geoip` 與 `asns` 規則需要）：

   執行內建腳本即可自動下載並原子替換 `GeoLite2-ASN.mmdb` 與 `GeoLite2-Country.mmdb` 至 `./data/geoip/`：

   ```bash
   ./tools/update_geoip.sh ./data/geoip
   ```

   *（若需動態同步最新 VPS ASN 清單，亦可執行 `./tools/update_vps_asns.sh ./vpsh_asns.yaml`，並於 `botPolicy.yaml` 與 `compose.yml` 中取消註解 `vpsh_asns.yaml` 的匯入與掛載。）*

3. **於 `.env` 設定金鑰與實例環境變數**：

   使用內建腳本自動產生 `QUART_SECRET_KEY` 與 `REDIS_PASSWORD` 並以 `0600` 權限寫入 `.env`：

   ```bash
   python3 tools/generate_secrets.py --write .env
   ```

   在 `compose.yml` 中：
   - 為 `app` 設定 `SITE_URL`（例如 `https://miku.example.com`），並視需要設定 Bilibili 憑證（`USE_CRED=true`、`SESSDATA`、`BILI_JCT`、`BUVID3`、`BUVID4`、`DEDEUSERID`）。
   - 將 `anubis` 的 `TARGET_HOST` 改為您的公開網域（例如 `miku.example.com`）。請保留 `COOKIE_DYNAMIC_DOMAIN=false`，確保 Anubis 僅核發限於目前主機的 Host-Only Cookie。
   - 保留 `TRUSTED_PROXIES=127.0.0.1/8,::1/128,172.30.0.0/24`，與 `miku_net` 子網路（`172.30.0.0/24`）保持一致。

4. **調整 `Caddyfile` 網域與憑證設定**：
   - 將 `yourdomain` 替換為您的公開網域名稱，並將 `email` 改為您的 ACME 聯絡信箱。
   - 若使用標準 Let's Encrypt 而非需要 EAB 的自訂 ACME CA，請移除全域區塊中的 `acme_ca` 與 `acme_eab`。
   - 確認主機上的 `/var/log/caddy` 目錄存在且 Caddy 容器具備寫入權限。

5. **啟動公開站點堆疊**：

   ```bash
   docker compose up -d
   ```

> [!NOTE]
> `example/docker/compose.yml.example` 將 `redis` 配置為純記憶體 `tmpfs` 快取（`40mb`、`allkeys-lru`、停用磁碟持久化），並將 `app` 的對外流量透過 `microwarp:1080`（`HTTP_PROXY`／`HTTPS_PROXY`）出口。若您的主機具備乾淨的家寬 IP 而不需要 WARP，可移除 `microwarp` 服務與對應的代理環境變數。

