# Operator Guide

`compose.yml` defines three services (plus optional WARP):

| Service | Role | Port |
| :--- | :--- | :--- |
| `app` | Quart app on Granian. All application logic. | 8080 (internal) |
| `caddy` | Reverse proxy and static file server. The public entry point. | 8000 |
| `redis` | Response cache and sessions. Required. | — |
| `warp` (optional) | Cloudflare WARP SOCKS5 egress. For datacenter IPs under risk control. | 1080 |

Request path: user → Caddy (8000) → Granian (8080) → Bilibili (direct or via WARP).

Reading order: [Quickstart](quickstart-docker) → [Configuration](configuration) →
[Reverse proxy](reverse-proxy) → [Credentials](credentials). Reference:
[Caching](caching), [Rate limiting](rate-limiting), [Maintenance](maintenance),
[Troubleshooting](troubleshooting).
