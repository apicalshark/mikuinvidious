# Maintenance

Logs:

```bash
docker compose logs -f app      # app
docker compose logs -f caddy    # reverse proxy
```

Granian access logs are noisy (one line per segment, image, poll).
`SERVER_ACCESS_LOG` controls them; off by default, keep it off.

Update:

```bash
git pull
docker compose up -d --build
```

Manual installs: `git pull`, `uv sync`, plus `npm run build:css` if the frontend
changed.

Back up:

- `config.toml` (or Compose env vars).
- Redis needs no backup (cache + sessions rebuild). Without a fixed
  `QUART_SECRET_KEY`, restarts invalidate sessions anyway — set one in production.
- With encrypted credentials, store `SECRETS_MASTER_KEY` separately from the config.
