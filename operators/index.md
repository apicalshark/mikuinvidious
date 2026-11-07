# Operator Guide

Running a MikuInvidious instance requires minimal infrastructure. The recommended deployment uses Docker Compose (`compose.yml`), which coordinates the following services:

---

## Service Overview

| Service | Role | Ports / Volumes |
| :--- | :--- | :--- |
| `app` | Granian ASGI running the Quart Python 3.15+ application. Mounts `img_cache:/app/data/img_cache`. | `8080` (internal) |
| `caddy` | Reverse proxy and static file server (`file_server`). Public HTTP/HTTPS gateway. | `8000` (HTTP), `443` (HTTPS) |
| `redis` | Database for page caching, DASH playurls, rate limiting, and tickets. *(User sessions are signed client-side cookies).* | `6379` (internal) |
| `warp` (optional) | Cloudflare WARP SOCKS5 egress (`1080`). Essential for datacenter IPs subject to Bilibili risk control. | `1080` (internal) |

---

## Architectural Data Flow

1. **Inbound Traffic**: Browsers connect to Caddy (`:8000` or `:443`).
2. **Static Assets**: Caddy serves `/static/*` directly from disk (`file_server`) with long-term caching and gzip/zstd compression.
3. **Application & Media**: Caddy passes dynamic routes to Granian (`:8080`), injecting client IP headers (`X-Real-IP`).
4. **Outbound Media & APIs**: Granian/Quart connects to upstream Bilibili APIs and CDNs either directly (for residential links) or via WARP (for datacenter hosts).

---

## Operator Reading Path

1. [Quickstart with Docker](quickstart-docker) — Spin up an instance in under two minutes.
2. [Configuration reference](configuration) — Exhaustive guide to `config.toml` and environment variables.
3. [Reverse proxy (Caddy)](reverse-proxy) — TLS termination, static files, and Anubis bot challenge setup.
4. [Bilibili credentials](credentials) — Unlocking 1080P60, 4K, and paid anime episodes with encrypted cookies.
5. [Caching](caching) — Redis TTL policies, dual-layer DASH cache, and image disk storage.
6. [Rate limiting](rate-limiting) — Token bucket sliding-window limits and trusted proxy CIDRs.
7. [Maintenance](maintenance) & [Troubleshooting](troubleshooting) — Logging, updates, and risk-control remedies.
