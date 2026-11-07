# Manual Installation

For operators and contributors running MikuInvidious directly on bare metal or custom VMs without Docker.

---

## 1. Prerequisites

- **Python**: Version 3.15 or newer
- **Package Manager**: [uv](https://astral.sh/uv) (fast Python package and project manager)
- **Database**: [Redis](https://redis.io/) (port 6379)
- **Web Server**: [Caddy](https://caddyserver.com/) (for TLS and static file serving)
- **Node.js**: Version 18+ (optional; required only if modifying Tailwind CSS or rebuilding frontend assets)
- **Media Tools**: [ffmpeg](https://ffmpeg.org/) (required for background download muxing)

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y python3 python3-venv git curl redis-server ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. Setup & Installation

1. **Clone the repository**:

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Sync Python dependencies**:

   ```bash
   uv sync
   ```

3. **Build frontend assets (optional)**:
   Precompiled styles are already tracked in git. If you plan to customize themes or recompile Tailwind CSS:

   ```bash
   npm install
   npm run build:css     # compiles Tailwind CSS 4 to static/themes/modern/css/main.css
   ```

   If running the stock theme as-is, you can safely skip this step.

4. **Initialize configuration**:

   ```bash
   cp config.toml.sample config.toml
   mkdir -p data/img_cache
   ```

5. **Configure `config.toml`**:
   - Set `site_url = "http://localhost:8888"` (or your public domain).
   - Set `[redis]` credentials or connection URL (`redis://localhost:6379`).
   - If running on a datacenter server, configure `proxy_url` under `[proxy]` pointing to a SOCKS5 proxy (e.g. WARP).

---

## 3. Launching the Server

Run Granian ASGI via the development launcher:

```bash
uv run python/main.py
```

The application will bind to the address specified in `config.toml` (default `http://[::]:8888` or `http://0.0.0.0:8888`). When running behind a reverse proxy such as Caddy, restrict direct backend access by setting `host = "127.0.0.1"` in `config.toml` or firewalling port 8888, ensuring public requests cannot bypass proxy protections.

For live-reloading during development:
Granian's reload mechanism is controlled by `[server] debug` in `config.toml`, which takes precedence over environment variables like `QUART_DEBUG`. To enable auto-reload:

1. Ensure the reload watcher is installed:

   ```bash
   uv add --dev watchfiles
   ```

2. Set `debug = true` under `[server]` in `config.toml`.
3. Launch the development server:

   ```bash
   uv run python/main.py
   ```

---

## 4. Reverse Proxy Setup

Set up Caddy to forward requests to `localhost:8888` and serve `./static` directly:

```text
:8000 {
    handle_path /static/* {
        root * ./static
        file_server
    }
    handle {
        reverse_proxy localhost:8888 {
            header_up X-Real-IP {remote_host}
        }
    }
}
```
