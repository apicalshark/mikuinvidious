# 反向代理（Caddy）

Caddy 為使用者實際連接的入口，負責 HTTPS 終止、靜態檔案服務，其餘請求一律轉發至 `app:8080`。

## 最小可用組態

```text
:8000 {
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
        reverse_proxy app:8080
    }
}
```

請注意 `encode` 排除 `/proxy/*`——影音串流為已壓縮的二進位內容，重新壓縮只會消耗 CPU 而無實益。

## 播放異常時停用 HTTP/3

若播放期間出現 `ERR_QUIC_PROTOCOL_ERROR`，成因多為瀏覽器 QUIC 協定棧處理 206 Partial Content 的相容性問題。請於 `Caddyfile` 頂端加入以下設定：

```text
{
    servers {
        protocols h1 h2
    }
}
```

重新啟動 Caddy 即可生效。此為已知問題，與使用者網路品質無關。

## 使用自有憑證

```text
mi.example.com {
    tls /path/to/cert.pem /path/to/key.pem
    ...
}
```

若無特殊需求，建議由 Caddy 自動申請（Let's Encrypt／ZeroSSL），無需額外設定。
