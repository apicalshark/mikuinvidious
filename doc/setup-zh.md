# 本地安装指南（非 Docker）

本指南提供了在不使用 Docker 的情况下，在本地机器上设置和运行 MikuInvidious 应用程序的分步说明。

此方法适用于无法或不想使用 Docker 的用户。它提供了对环境最直接的控制，但需要手动安装和配置所有系统依赖项。

## 前置条件

在开始之前，请确保您已安装并运行以下组件：

- **Python 3.10+**
- **Node.js v18+ 和 npm**（仅用于开发）
- **Git**
- **uv**（Python 包管理器，通过 `pip install uv` 安装）
- **Redis**
- **Caddy**（用于反向代理，请参阅 [caddyserver.com/docs/install](https://caddyserver.com/docs/install)）
- **Cloudflare WARP 桌面客户端**

---

## 步骤 1：安装并配置依赖项

应用程序需要运行三个后台服务：Redis、Cloudflare WARP 和 Caddy。

### 1. 安装并运行 Redis

Redis 用于缓存和会话存储。
请遵循官方 [Redis 安装指南](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/)。

确认 Redis 正在其默认端口 `6379` 上运行。

### 2. 安装并配置 Cloudflare WARP

应用程序需要 Cloudflare WARP 客户端作为 SOCKS5 代理，以便访问 Bilibili 内容。

1. **安装 WARP 客户端：**
   从 [Cloudflare 1.1.1.1 网站](https://1.1.1.1/) 下载并安装适用于您操作系统的官方客户端。

2. **启用本地代理模式：**
   - 打开 WARP 客户端的 **Preferences**（首选项）或 **Settings**（设置）面板。
   - 导航到 **Advanced**（高级）选项卡。
   - 点击 **Configure Proxy**（配置代理）。
   - 勾选 **Enable local proxy**（启用本地代理）复选框。
   - 将 **Port**（端口）设置为 `1080`。
   - 保存更改。
   - 返回 WARP 主设置界面，选择新的 **"WARP via Local Proxy"**（通过本地代理的 WARP）模式。

您的 WARP 客户端现在正在端口 `1080` 上监听 SOCKS5 连接。

### 3. 配置 Caddy

Caddy 将作为您的 Web 服务器和反向代理。

1. **安装 Caddy**：请按照 [caddyserver.com/docs/install](https://caddyserver.com/docs/install) 中适用于您操作系统的说明进行安装。
2. **在项目根目录下创建一个 `Caddyfile`**。

    **本地测试 (HTTP):**

    ```caddy
    :8000 {
        handle /static/* {
            root * ./static
            file_server
        }
        reverse_proxy localhost:8888
    }
    ```

    **生产环境 (HTTPS 自动 SSL):**
    *注意：需要开放 80 和 443 端口，并将域名解析到此服务器。*

    ```caddy
    yourdomain.com {
        handle /static/* {
            root * ./static
            file_server
        }
        reverse_proxy localhost:8888
    }
    ```

3. **运行 Caddy**：

    ```bash
    caddy start
    ```

---

## 步骤 2：设置应用程序

1. **克隆代码仓库：**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **构建 TailwindCSS（仅用于开发）：**

   ```bash
   npm run build:css
   ```

---

## 步骤 3：配置应用程序

创建并编辑本地配置文件，将应用程序连接到您手动配置的服务。

1. **创建 `config.toml`：**

   ```bash
   cp config.toml.sample config.toml
   ```

2. **编辑 `config.toml`：**
   打开文件并进行以下更改：
   - 在 `[server]` 下，设置一个唯一的 `secret_key`。可以使用以下命令生成：
     `python -c 'import secrets; print(secrets.token_hex(16))'`

   - 在 `[redis]` 下，确保 `url` 指向您的本地 Redis 实例：

     ```toml
     url = "redis://localhost:6379"
     ```

   - 在 `[proxy]` 下，确保 `proxy_url` 指向您的 WARP 客户端：

     ```toml
     proxy_url = "socks5://localhost:1080"
     ```

---

## 步骤 4：运行应用程序

在依赖项正常运行且配置完成后，启动服务器：

```bash
uv run python/main.py
```

应用程序将在 **`http://localhost:8888`**（`config.toml.sample` 中的默认端口）上可用。
