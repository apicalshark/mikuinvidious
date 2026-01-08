# 使用 Docker 进行生产环境部署

本指南介绍了如何使用 Docker Compose 和 Caddy 在生产环境中部署 MikuInvidious。

## 前置条件

- 已安装 **Docker** 和 **Docker Compose**。
- 一个域名（如果使用 HTTPS）。
- 防火墙已开放 **80**、**443 (TCP/UDP)** 和 **8000** 端口。

---

## 1. 快速启动（生产环境）

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

---

## 2. SSL 证书管理

Caddy 默认自动处理 SSL/TLS 证书。

### 自动 SSL（推荐）

Caddy 将自动从 Let's Encrypt 或 ZeroSSL 获取并续订证书。

- **配置：** 只需在 `Caddyfile` 中将站点地址设为您的域名即可。
- **自动重定向：** Caddy 会自动将所有 HTTP 流量（80 端口）重定向到 HTTPS（443 端口）。

### 手动证书

如果您已经有证书（例如 Cloudflare Origin CA），请按照以下步骤操作：

1. **将证书放入** `ssl/` 目录（例如 `ssl/cert.pem` 和 `ssl/key.pem`）。
2. **更新 `compose.yml`** 以挂载 SSL 目录：

   ```yaml
   caddy:
     volumes:
       - ./ssl:/etc/caddy/ssl:ro
       # ... 其他卷
   ```

3. **更新 `Caddyfile`** 以使用您的证书：

   ```caddy
   yourdomain.com {
       tls /etc/caddy/ssl/cert.pem /etc/caddy/ssl/key.pem
       # ... 其他配置
   }
   ```

---

## 3. HTTP/3 (QUIC) 支持

Caddy 原生支持 HTTP/3。请确保防火墙已开放 **443/UDP** 端口，并在 `compose.yml` 中正确映射。

---

## 4. 维护

- **查看日志：** `docker compose logs -f caddy`
- **更新应用：**

  ```bash
  git pull
  docker compose up -d --build
  ```
