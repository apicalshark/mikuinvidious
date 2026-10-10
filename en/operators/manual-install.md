# Manual installation

For running without Docker. Requires Python 3.14+, Redis, `uv`.

```bash
sudo apt update
sudo apt install python3 python3-venv git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Also install [Redis](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-linux/)
(port 6379) and [Caddy](https://caddyserver.com/docs/install). CSS work needs
Node.js 18+.

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
uv sync
npm install
npm run build:css        # only after CSS changes
cp config.toml.sample config.toml
```

Edit `config.toml`: `secret` under `[server]` (generated at each start if unset;
sessions die on restart), `url = "redis://localhost:6379"` under `[redis]`,
`proxy_url` under `[proxy]` on datacenter hosts.

```bash
uv run python/main.py
```

Listens on `http://localhost:8888` (8000 behind Caddy):

```text
:8000 {
    handle /static/* {
        root * ./static
        file_server
    }
    reverse_proxy localhost:8888
}
```

Development: `QUART_DEBUG=true uv run python/main.py` (auto-reload).

Before a PR:

```bash
npm run lint      # ruff + prettier + djlint
npm run format
```
