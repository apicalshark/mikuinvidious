# Manual installation

For developers who skip Docker and hack on the code directly. Requirements:
Python 3.14+, Redis, and the `uv` package manager.

## System dependencies (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Also install [Redis](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)
(listening on 6379 is fine) and [Caddy](https://caddyserver.com/docs/install).
Frontend style work additionally needs Node.js 18+.

## Steps

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
uv sync
npm install
npm run build:css        # compile Tailwind (only needed after CSS changes)
cp config.toml.sample config.toml
```

Edit `config.toml`: set `secret` under `[server]` (auto-generated at each start
if unset, but sessions die on restart); confirm
`url = "redis://localhost:6379"` under `[redis]`; on datacenter hosts, set
`proxy_url` under `[proxy]`.

```bash
uv run python/main.py
```

The app listens on `http://localhost:8888` by default, port 8000 via Caddy:

```text
:8000 {
    handle /static/* {
        root * ./static
        file_server
    }
    reverse_proxy localhost:8888
}
```

During development, enable debug mode for auto-reload:
`QUART_DEBUG=true uv run python/main.py`.

## Pre-submit checks

```bash
npm run lint      # full ruff + prettier + djlint check
npm run format    # auto-format before opening a PR
```
