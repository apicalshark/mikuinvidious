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
- **User Sessions**: Quart cryptographically signs client-side session cookies with an ephemeral secret key generated per startup, but does not encrypt their contents. Restarting the server intentionally invalidates sessions.
- **Image Disk Cache**: The `img_cache` directory automatically rebuilds on demand and prunes itself via LRU policies.
