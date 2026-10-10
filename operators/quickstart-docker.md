# 使用 Docker 快速啟動

Docker 部署為官方建議的方式，Redis 與 Caddy 均已預先配置完成。

## 前置需求

- 已安裝 Docker 及 Docker Compose。
- 網域名稱（如需使用 HTTPS）；防火牆需開放 80、443（TCP／UDP）連接埠。

## 啟動步驟

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
cp Caddyfile.example Caddyfile
docker compose up -d
```

開啟 `http://localhost:8000`，若首頁正常顯示，即表示部署成功。

## 綁定網域

1. 將 `compose.yml` 中 `app` 服務的 `SITE_URL` 修改為實際網域（例如 `https://mi.example.com`）。
2. 將 `Caddyfile` 首行修改為該網域：

```text
mi.example.com {
    handle /static/* {
        root * /usr/share/caddy
        file_server
    }
    reverse_proxy app:8080
}
```

3. 執行 `docker compose up -d` 重新啟動，Caddy 將自動向 Let's Encrypt／ZeroSSL 申請並續用憑證。

## 資料中心 IP 的注意事項

若主機位於資料中心（例如 Hetzner、OVH），Bilibili 將實施風險控制（HTTP 412、`-352`），導致空間頁面與搜尋結果為空。此時須設定 WARP 出口，詳見[故障排除](troubleshooting.md)。家用寬頻連線一般可直接連接，無需代理。
