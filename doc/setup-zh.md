# MikuInvidious 设置与开发指南

本指南提供了使用 Docker 进行生产环境部署，或进行本地开发设置的说明。

---

## 1. 前置条件与核心工具

在开始之前，请确保您的系统已安装以下工具。无论是本地开发还是手动安装，这些都是必需的。

### 系统依赖 (Linux/Debian/Ubuntu)

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
```

### 核心工具与安装链接

- **Python 3.14+**: 后端运行所需。
- **uv**: 推荐的 Python 包管理器。安装命令：
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  *或者通过 pip 安装：`pip install uv`*
- **Redis**: 用于缓存和会话存储。[安装指南](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)
- **Caddy**: 高性能反向代理。[安装指南](https://caddyserver.com/docs/install)
- **Node.js v18+ & npm**: 构建 CSS 和前端工具所需。[安装指南](https://nodejs.org/en/download/package-manager)

---

## 2. 本地开发

如果您打算为 MikuInvidious 贡献代码或修改源代码，请按照以下步骤设置开发环境。

### 设置步骤

1. **安装依赖：**
   ```bash
   uv sync
   npm install
   ```

2. **前端资产：**
   项目使用 Tailwind CSS。要构建 CSS 文件，请运行：
   ```bash
   npm run build:css
   ```

3. **在调试模式下运行：**
   对于开发，建议开启调试模式以启用自动重载和详细错误消息：
   ```bash
   QUART_DEBUG=true uv run python/main.py
   ```

4. **代码检查与格式化：**
   在提交更改之前，请确保您的代码符合项目风格：
   ```bash
   # 检查所有代码
   npm run lint
   
   # 自动格式化所有代码
   npm run format
   ```

---

## 3. Docker 部署（推荐用于生产环境）

这是运行 MikuInvidious 最简单、最稳定的方式。它包含了所有必要的预配置依赖项（Redis、Caddy、Cloudflare WARP）。

### 前置条件

- 已安装 **Docker** 和 **Docker Compose**。
- 一个域名（如果使用 HTTPS）。
- 防火墙已开放 **80**、**443 (TCP/UDP)** 端口。

### 快速启动

1. **克隆代码仓库：**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **配置 `compose.yml`：**
   将 `app` 服务中的 `SITE_URL` 更新为您的域名：

   ```yaml
   environment:
     - SITE_URL=https://yourdomain.com
   ```

3. **配置 `Caddyfile`：**
   将第一行更改为您的域名：

   ```caddy
   yourdomain.com {
       handle /static/* {
           root * /usr/share/caddy
           file_server
       }
       reverse_proxy app:8080
   }
   ```

4. **启动：**

   ```bash
   docker compose up -d
   ```

### SSL 证书管理

Caddy 默认自动处理 SSL/TLS 证书。

- **自动 SSL（推荐）：** Caddy 将自动从 Let's Encrypt 或 ZeroSSL 获取并续订证书。只需在 `Caddyfile` 中使用您的域名即可。
- **手动证书：** 如果您已有证书，请在 `compose.yml` 中挂载它们，并在 `Caddyfile` 中使用 `tls` 指令进行配置。

**关于 HTTP/3 (QUIC) 的重要提示：**
如果您在视频播放期间遇到 `ERR_QUIC_PROTOCOL_ERROR`，建议在 Caddy 中**禁用 HTTP/3**。这通常是由于现代浏览器在通过 QUIC 代理媒体流时处理 206 Partial Content 的方式引起的。您可以通过在 `Caddyfile` 顶部添加以下内容来禁用它：

```caddy
{
    servers {
        protocols h1 h2
    }
}
```

---

## 4. 本地手动安装（开发/进阶）

此方法可以对环境进行直接控制，但需要手动配置所有依赖项。

### 步骤 1：安装并配置依赖项

1. **Redis：** 确保其在 `6379` 端口运行。
2. **Caddy：**
   - 在项目根目录创建 `Caddyfile`：
     ```caddy
     :8000 {
         handle /static/* {
             root * ./static
             file_server
         }
         reverse_proxy localhost:8888
     }
     ```
   - 启动 Caddy：`caddy start`

### 步骤 2：设置应用程序

1. **克隆并同步：**
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   uv sync
   ```
2. **构建 TailwindCSS（可选）：**
   ```bash
   npm run build:css
   ```
3. **配置：**
   ```bash
   cp config.toml.sample config.toml
   ```
   编辑 `config.toml`：
   - 在 `[server]` 中设置 `secret_key`。
   - 确保 `[redis]` 中的 `url = "redis://localhost:6379"`。
   - 如果您使用 SOCKS5/HTTP 代理，请配置 `[proxy]` 中的 `proxy_url`。

### 步骤 3：运行

```bash
uv run python/main.py
```

应用程序将通过 `http://localhost:8888`（或通过 Caddy 访问端口 8000）可用。

---

## 5. 配置说明

有关所有配置选项的详细信息，请参阅 [configuration.md](./configuration.md)。

**提示（身份验证）：** 如果您需要访问 1080P 或会员功能，请在 `config.toml` 的 `[credential]` 部分填入您的 Bilibili Cookie，并设置 `use_cred = true`。

---

## 6. 维护

- **查看日志 (Docker):** `docker compose logs -f app`
- **查看日志 (Caddy):** `docker compose logs -f caddy`
- **更新应用 (Docker):**
  ```bash
  git pull
  docker compose up -d --build
  ```
- **更新应用 (手动):**
  ```bash
  git pull
  uv sync
  ```
