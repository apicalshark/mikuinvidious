# 手動安裝

本章節適用於在裸機或自建虛擬機器上直接執行 MikuInvidious、不使用 Docker 的站長與貢獻者。

---

## 1. 系統需求

- **Python**：3.15 或更新版本
- **套件管理工具**：[uv](https://astral.sh/uv)（極速 Python 套件管理器）
- **資料庫**：[Redis](https://redis.io/)（埠號 6379）
- **Web 伺服器**：[Caddy](https://caddyserver.com/)（處理 TLS 憑證與靜態資源）
- **Node.js**：18+ 版本（僅於需要編譯前端 CSS 或同步靜態資源時需要）
- **媒體工具**：[ffmpeg](https://ffmpeg.org/)（背景下載合併音視訊軌道必備）

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y python3 python3-venv git curl redis-server ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. 下載與安裝流程

1. **複製程式碼庫**：
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **同步 Python 相依套件**：
   ```bash
   uv sync
   ```

3. **安裝前端相依套件與編譯樣式**：
   ```bash
   npm install
   npm run build:css     # 將 Tailwind CSS 4 編譯為 static/themes/modern/css/main.css
   ```

4. **初始化組態設定**：
   ```bash
   cp config.toml.sample config.toml
   mkdir -p data/img_cache
   ```

5. **編輯 `config.toml`**：
   - 設定 `site_url = "http://localhost:8888"`（或您的實際公開網域）。
   - 設定 `[redis]` 連線網址（`redis://localhost:6379`）。
   - 若伺服器位於資料中心，於 `[proxy]` 下配置 `proxy_url` 指向 SOCKS5 代理（如 WARP）。

---

## 3. 啟動應用程式伺服器

透過開發執行腳本啟動 Granian ASGI：

```bash
uv run python/main.py
```

伺服器將監聽於 `http://0.0.0.0:8888`。

開發除錯模式（支援程式碼熱重載）：
```bash
QUART_DEBUG=true uv run python/main.py
```

---

## 4. 反向代理配置

設定 Caddy 反向代理至 `localhost:8888` 並直接傳遞 `./static` 資源：

```text
:8000 {
    root * ./static
    handle /static/* {
        file_server
    }
    handle {
        reverse_proxy localhost:8888 {
            header_up X-Real-IP {remote_host}
        }
    }
}
```
