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

    # 直接自磁碟提供靜態資源（完全不經過 Python 應用程式）
    handle /static/* {
        header X-Content-Type-Options "nosniff"
        header Cache-Control "public, max-age=86400"
        file_server
    }

    handle /site.webmanifest {
        root * /usr/share/caddy/static
        header Content-Type "application/manifest+json"
        file_server
    }

    # 壓縮 HTML、JSON 與文字內容（嚴禁對媒體串流重複壓縮）
    @notProxy not path /proxy/*
    encode @notProxy zstd gzip

    # 將其餘所有動態請求轉發給 Python 應用程式
    handle {
        reverse_proxy app:8080 {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
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
Granian 位於 Caddy 後方。為確保速率限制器與日誌能取得訪客的真實公網 IP 而非 Docker 內部橋接 IP，Caddy 必須設定：

```text
reverse_proxy app:8080 {
    header_up X-Real-IP {remote_host}
}
```

---

## 3. 搭配 Anubis PoW 進行高流量機器人防禦

針對遭受大量爬蟲抓取或分散式流量攻擊的公開站點，MikuInvidious 提供開箱即用的 Anubis 工作量證明（Proof-of-Work, PoW）防護設定（參見 `tools/botPolicy.yaml.example` 與 `tools/Caddyfile.prod.example`）。

```
[ 瀏覽器 ／ 爬蟲客戶端 ]
         |
         v
    [ Caddy ]
         | (靜態資源 /static/* 直接放行回應)
         v
    [ Anubis PoW 閘道 ] -- 針對可疑爬蟲與無頭腳本發起 PoW 算力驗證
         |
         v (通過驗證的真實瀏覽器)
    [ Granian / Quart ]
```

- **靜態資源白名單**：`/static/*` 與健康檢查路徑全面繞過挑戰。
- **圖片代理防護（`/proxy/pic/*`）**：由 Anubis PoW 挑戰保護；在通過挑戰後，由 Python 直接自本機磁碟以 `< 1ms` 極速回應。
- **針對性防禦規則**：自動阻擋探測漏洞之後綴（`.env`、`.git`、`.php`）、拒絕惡意 AI 爬蟲，並對資料中心與商業 VPN ASN 發送微量算力挑戰。
