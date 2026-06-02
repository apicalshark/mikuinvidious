# MikuInvidious Setup & Development Guide

This guide provides instructions for setting up MikuInvidious for production using Docker, or for local development.

---

## 1. Prerequisites & Key Tools

Before you begin, ensure your system has the following tools installed. These are required for both local development and manual installation.

### System Dependencies (Linux/Debian/Ubuntu)

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
```

### Key Tools & Installation Links

- **Python 3.14+**: Required for the backend.
- **uv**: The recommended Python package manager. Install it via:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  *Or via pip: `pip install uv`*
- **Redis**: Required for caching and session storage. [Install Guide](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)
- **Caddy**: High-performance reverse proxy. [Install Guide](https://caddyserver.com/docs/install)
- **Node.js v18+ & npm**: Required for building CSS and frontend tools. [Install Guide](https://nodejs.org/en/download/package-manager)

---

## 2. Local Development

If you intend to contribute to MikuInvidious or modify the source code, follow these steps to set up your hacking environment.

### Setup Steps

1. **Install Dependencies:**
   ```bash
   uv sync
   npm install
   ```

2. **Frontend Assets:**
   The project uses Tailwind CSS. To build the CSS file:
   ```bash
   npm run build:css
   ```

3. **Running in Debug Mode:**
   For development, it's recommended to enable debug mode for auto-reloading and detailed error messages:
   ```bash
   QUART_DEBUG=true uv run python/main.py
   ```

4. **Linting and Formatting:**
   Before submitting changes, ensure your code follows the project's style:
   ```bash
   # Lint everything
   npm run lint
   
   # Automatically format everything
   npm run format
   ```

---

## 3. Docker Deployment (Recommended for Production)

This is the easiest and most stable way to run MikuInvidious. It includes all necessary dependencies (Redis, Caddy, Cloudflare WARP) pre-configured.

### Prerequisites

- **Docker** and **Docker Compose** installed.
- A domain name (if using HTTPS).
- Ports **80**, **443 (TCP/UDP)** open on your firewall.

### Quick Start

1. **Clone the repository:**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Configure `compose.yml`:**
   Update the `SITE_URL` in the `app` service to your domain:

   ```yaml
   environment:
     - SITE_URL=https://yourdomain.com
   ```

3. **Configure `Caddyfile`:**
   Change the first line to your domain name:

   ```caddy
   yourdomain.com {
       handle /static/* {
           root * /usr/share/caddy
           file_server
       }
       reverse_proxy app:8080
   }
   ```

4. **Launch:**

   ```bash
   docker compose up -d
   ```

### SSL Management

Caddy handles SSL/TLS certificates automatically by default.

- **Automatic SSL (Recommended):** Caddy will automatically obtain and renew certificates from Let's Encrypt or ZeroSSL. Simply use your domain name in the `Caddyfile`.
- **Manual Certificates:** If you have existing certificates, mount them in `compose.yml` and update `Caddyfile` with the `tls` directive.

**Important Note on HTTP/3 (QUIC):**
It is recommended to **disable HTTP/3** in Caddy if you experience `ERR_QUIC_PROTOCOL_ERROR` during video playback. This is often caused by how modern browsers handle 206 Partial Content via QUIC when proxying media streams. You can disable it by adding the following to the top of your `Caddyfile`:

```caddy
{
    servers {
        protocols h1 h2
    }
}
```

---

## 4. Manual Installation (Development/Advanced)

This approach provides direct control over the environment but requires manual configuration of all dependencies.

### Step 1: Install and Configure Dependencies

1. **Redis:** Ensure it is running on port `6379`.
2. **Caddy:**
   - Create a `Caddyfile` in the project root:
     ```caddy
     :8000 {
         handle /static/* {
             root * ./static
             file_server
         }
         reverse_proxy localhost:8888
     }
     ```
   - Start Caddy: `caddy start`

### Step 2: Set Up the Application

1. **Clone and Sync:**
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   uv sync
   ```
2. **Build TailwindCSS (optional):**
   ```bash
   npm run build:css
   ```
3. **Configure:**
   ```bash
   cp config.toml.sample config.toml
   ```
   Edit `config.toml`:
   - Set `secret_key` in `[server]`.
   - Ensure `url = "redis://localhost:6379"` in `[redis]`.
   - Configure `proxy_url` in `[proxy]` if you are using a SOCKS5/HTTP proxy.

### Step 3: Run

```bash
uv run python/main.py
```

The application will be available at `http://localhost:8888` (or port 8000 via Caddy).

---

## 5. Configuration

For detailed information on all configuration options, see [configuration.md](./configuration.md).

**Tip (Authentication):** If you need to access 1080P or premium features, fill in your Bilibili Cookies in the `[credential]` section of `config.toml` and set `use_cred = true`.

---

## 6. Maintenance

- **View Logs (Docker):** `docker compose logs -f app`
- **View Logs (Caddy):** `docker compose logs -f caddy`
- **Update Application (Docker):**
  ```bash
  git pull
  docker compose up -d --build
  ```
- **Update Application (Manual):**
  ```bash
  git pull
  uv sync
  ```
