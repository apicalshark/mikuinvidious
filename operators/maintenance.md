# Maintenance & Operations

Guidelines for inspecting logs, upgrading releases, and backing up instance configuration.

---

## 1. Inspecting Logs

To view application and proxy logs in real time:

```bash
docker compose logs -f app      # Quart application & DASH streaming engine
docker compose logs -f caddy    # Caddy access and TLS logs
docker compose logs -f redis    # Redis cache operations
```

> [!TIP]
> Granian per-request access logs are intentionally disabled by default (`SERVER_ACCESS_LOG=false`). Enabling them generates excessive log lines due to continuous DASH media fragment requests, image fetching, and download polling.

---

## 2. Upgrading to New Releases

To pull the latest improvements, fixes, and dependencies:

```bash
git pull
docker compose build --pull app
docker compose up -d
```

For manual (non-Docker) bare-metal installations:

```bash
git pull
uv sync
npm install
npm run build:css     # if frontend styles updated
```

After these commands complete, restart the application using your process manager or deployment startup command so the running process uses the new release.

---

## 3. Backup Strategy

What you should back up:

- **Configuration**: Your `config.toml` file (or custom environment variables in `compose.yml`).
- **TLS Data**: Caddy's certificate storage volume (`caddy_data`) if you want to avoid re-issuance rate limits when moving hosts.
- **Master Encryption Key**: If you encrypted credentials with `tools/encrypt_secrets.py`, store `SECRETS_MASTER_KEY` in a secure password manager separate from your codebase.

What does **not** need backup:

- **Redis**: Redis holds transient caches, temporary tickets, and rate-limiting counters. It can be cleared or discarded at any time.
- **User Sessions**: Quart cryptographically signs client-side session cookies and CSRF tokens. When `QUART_SECRET_KEY` (or `[server].secret_key`) is unset, an ephemeral key is generated at startup and restarts invalidate sessions; set `QUART_SECRET_KEY` in production if you want sessions to survive restarts.
- **Image Disk Cache**: The `img_cache` directory automatically rebuilds on demand and prunes itself via LRU policies.

---

## 4. Production Operator Helper Scripts (`tools/`)

The `tools/` directory includes ready-to-use scripts for bootstrapping and maintaining a production server:

| Script / Directory | Purpose | Example Usage |
| :--- | :--- | :--- |
| `tools/generate_secrets.py` | Generates `QUART_SECRET_KEY`, `REDIS_PASSWORD`, and optionally `SECRETS_MASTER_KEY`, writing `.env` with `0600` permissions without clobbering existing keys unless `--force` is passed. | `python3 tools/generate_secrets.py --include-master-key --write .env` |
| `tools/encrypt_secrets.py` | Encrypts Bilibili cookies (`SESSDATA`, `BILI_JCT`, `BUVID3`, `BUVID4`, `DEDEUSERID`) using `SECRETS_MASTER_KEY` for safe storage in `config.toml` or `.env`. | `uv run python tools/encrypt_secrets.py` |
| `tools/update_geoip.sh` | Downloads and atomically replaces `GeoLite2-ASN.mmdb` and `GeoLite2-Country.mmdb` (from `P3TERX/GeoLite.mmdb`) so Anubis's memory-mapped reader never sees partial writes. Ideal for cron jobs. | `./tools/update_geoip.sh ./data/geoip` |
| `tools/update_vps_asns.sh` | Fetches the latest `vpsh.txt` datacenter/VPS ASN list (explicitly excluding Cloudflare WARP ASNs `13335` and `209242`) and writes `vpsh_asns.yaml` for Anubis `botPolicy.yaml`. | `./tools/update_vps_asns.sh ./vpsh_asns.yaml` |
| `tools/warp/` | Standalone Cloudflare WARP (`microwarp`) Docker Compose stacks for datacenter hosts: `tools/warp/compose.yml.example` (host-local) and `tools/warp/app/` (container-to-container `warp-net` override). | `cp tools/warp/app/compose.yml.example tools/warp/app/compose.yml` |

