# Operator Guide — Overview

This guide is for people deploying and running a MikuInvidious instance.
Deployment is straightforward, but a few concepts help first.

## Services

`compose.yml` defines three services (plus an optional WARP service):

| Service | Role | Exposed port |
| :--- | :--- | :--- |
| `app` | Runs the Quart app on Granian; all application logic lives here | 8080 (internal) |
| `caddy` | Reverse proxy and static file server; the entry point users connect to | 8000 |
| `redis` | Caches API responses and holds sessions; required | — |
| `warp` (optional) | Cloudflare WARP SOCKS5 egress for datacenter IPs to bypass risk control | 1080 |

Request path: user → Caddy (8000) → Granian (8080) → Bilibili (direct, or via WARP).

## Suggested reading order

1. [Quickstart with Docker](quickstart-docker) — get running first.
2. [Configuration reference](configuration) — the full option list.
3. [Reverse proxy](reverse-proxy) — bind a domain and enable HTTPS.
4. [Bilibili credentials](credentials) — whether you need login credentials and how to set them.
5. The rest as reference: [Caching](caching), [Rate limiting](rate-limiting),
   [Maintenance](maintenance), [Troubleshooting](troubleshooting).
