# MikuInvidious 設置與開發指南

本指南提供了使用 Docker 進行生產環境部署，或進行本地開發設置的說明。

---

## 1. 前置條件與核心工具

在開始之前，請確保您的系統已安裝以下工具。無論是本地開發還是手動安裝，這些都是必需的。

### 系統依賴 (Linux/Debian/Ubuntu)

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
```

### 核心工具與安裝連結

- **Python 3.14+**: 後端執行所需。
- **uv**: 推薦的 Python 套件管理器。安裝命令：
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  *或者透過 pip 安裝：`pip install uv`*
- **Redis**: 用於快取和工作階段存儲。[安裝指南](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)
- **Caddy**: 高性能反向代理。[安裝指南](https://caddyserver.com/docs/install)
- **Node.js v18+ & npm**: 建置 CSS 和前端工具所需。[安裝指南](https://nodejs.org/en/download/package-manager)

---

## 2. 本地開發

如果您打算為 MikuInvidious 貢獻程式碼或修改原始碼，請按照以下步驟設置開發環境。

### 設置步驟

1. **安裝依賴：**
   ```bash
   uv sync
   npm install
   ```

2. **前端資產：**
   專案使用 Tailwind CSS。要建置 CSS 檔案，請執行：
   ```bash
   npm run build:css
   ```

3. **在偵錯模式下執行：**
   對於開發，建議開啟偵錯模式以啟用自動重載和詳細錯誤訊息：
   ```bash
   QUART_DEBUG=true uv run python/main.py
   ```

4. **程式碼檢查與格式化：**
   在提交更改之前，請確保您的程式碼符合專案風格：
   ```bash
   # 檢查所有程式碼
   npm run lint
   
   # 自動格式化所有程式碼
   npm run format
   ```

---

## 3. Docker 部署（推薦用於生產環境）

這是執行 MikuInvidious 最簡單、最穩定的方式。它包含了所有必要的預配置依賴項（Redis、Caddy）。

### 前置條件

- 已安裝 **Docker** 和 **Docker Compose**。
- 一個網域名稱（如果使用 HTTPS）。
- 防火牆已開放 **80**、**443 (TCP/UDP)** 連接埠。

### 快速啟動

1. **複製程式碼倉庫：**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **配置 `compose.yml`：**
   將 `app` 服務中的 `SITE_URL` 更新為您的網域：

   ```yaml
   environment:
     - SITE_URL=https://yourdomain.com
   ```

3. **配置 `Caddyfile`：**
   將第一行更改為您的網域名稱：

   ```caddy
   yourdomain.com {
       handle /static/* {
           root * /usr/share/caddy
           file_server
       }
       reverse_proxy app:8080
   }
   ```

4. **啟動：**

   ```bash
   docker compose up -d
   ```

### SSL 憑證管理

Caddy 預設自動處理 SSL/TLS 憑證。

- **自動 SSL（推薦）：** Caddy 將自動從 Let's Encrypt 或 ZeroSSL 獲取並續訂憑證。只需在 `Caddyfile` 中使用您的網域名稱即可。
- **手動憑證：** 如果您已有憑證，請在 `compose.yml` 中掛載它們，並在 `Caddyfile` 中使用 `tls` 指令進行配置。

**關於 HTTP/3 (QUIC) 的重要提示：**
如果您在影片播放期間遇到 `ERR_QUIC_PROTOCOL_ERROR`，建議在 Caddy 中**停用 HTTP/3**。這通常是由於現代瀏覽器在透過 QUIC 代理媒體串流時處理 206 Partial Content 的方式引起的。您可以透過在 `Caddyfile` 頂部添加以下內容來停用它：

```caddy
{
    servers {
        protocols h1 h2
    }
}
```

---

## 4. 本地手動安裝（開發/進階）

此方法可以對環境進行直接控制，但需要手動配置所有依賴項。

### 步驟 1：安裝並配置依賴項

1. **Redis：** 確保其在 `6379` 連接埠執行。
2. **Caddy：**
   - 在專案根目錄建立 `Caddyfile`：
     ```caddy
     :8000 {
         handle /static/* {
             root * ./static
             file_server
         }
         reverse_proxy localhost:8888
     }
     ```
   - 啟動 Caddy：`caddy start`

### 步驟 2：設置應用程式

1. **複製與同步：**
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   uv sync
   ```
2. **建置 TailwindCSS（可選）：**
   ```bash
   npm run build:css
   ```
3. **配置：**
   ```bash
   cp config.toml.sample config.toml
   ```
   編輯 `config.toml`：
   - 在 `[server]` 中設置 `secret_key`。
   - 確保 `[redis]` 中的 `url = "redis://localhost:6379"`。
   - 如果您使用 SOCKS5/HTTP 代理，請配置 `[proxy]` 中的 `proxy_url`。

### 步驟 3：執行

```bash
uv run python/main.py
```

應用程式將透過 `http://localhost:8888`（或透過 Caddy 存取連接埠 8000）可用。

---

## 5. 配置說明

有關所有配置選項的詳細資訊，請參閱 [configuration.md](./configuration.md)。

**提示（身份驗證）：** 如果您需要存取 1080P 或會員功能，請在 `config.toml` 的 `[credential]` 部分填入您的 Bilibili Cookie，並設置 `use_cred = true`。

---

## 6. 維護

- **查看日誌 (Docker):** `docker compose logs -f app`
- **查看日誌 (Caddy):** `docker compose logs -f caddy`
- **更新應用 (Docker):**
  ```bash
  git pull
  docker compose up -d --build
  ```
- **更新應用 (手動):**
  ```bash
  git pull
  uv sync
  ```
