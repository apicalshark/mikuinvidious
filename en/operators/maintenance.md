# Maintenance

## Logs

```bash
docker compose logs -f app      # app
docker compose logs -f caddy    # reverse proxy
```

Granian access logs are voluminous (one line per media segment, image, and poll).
They're controlled by `SERVER_ACCESS_LOG`, off by default — keep it off.

## Updates

```bash
git pull
docker compose up -d --build
```

Manual installs: `git pull`, then `uv sync`. If the frontend changed, also run
`npm run build:css`.

## What to back up

- `config.toml` (or the Compose environment variables): the heart of the site config.
- Redis: losing it only means rebuilding cache and sessions — no backup needed.
  But without a fixed `QUART_SECRET_KEY`, restarts invalidate sessions anyway;
  production sites must set one.
- Credentials: when stored encrypted, keep `SECRETS_MASTER_KEY` separate from
  the config file.
