# WARP for the containerized app

Runs Cloudflare WARP (MicroWARP) on a shared Docker network so the real
`miku_app` container reaches Bilibili through `socks5://microwarp:1080`
with pure container-to-container traffic. Nothing listens on the LAN —
both published ports stay on host loopback (for debugging only).

For host-local use without Docker networking, see `../compose.yml.example`
instead.

## Files

| File | Purpose |
|---|---|
| `compose.yml.example` | Warp stack variant: local wrapper build, single-homed on `warp-net` |
| `Dockerfile` | MicroWARP plus `entrypoint-wrap.sh` (rebuild with `--pull` for upstream updates) |
| `entrypoint-wrap.sh` | Adds same-subnet LAN return routes every boot (see below) |
| `miku-warp.override.yml.example` | App-side override: join `warp-net`, set `HTTP_PROXY` |

## Usage

From the repo root:

```bash
# Once: shared network both stacks join
docker network create warp-net

# Warp stack (copy the example first, then build + start)
cp tools/warp/app/compose.yml.example tools/warp/app/compose.yml
docker compose -f tools/warp/app/compose.yml up -d --build

# App through the tunnel (main compose.yml stays untouched for direct users)
docker compose -f compose.yml -f tools/warp/app/miku-warp.override.yml.example up -d --build
```

Verify:

```bash
docker logs microwarp 2>&1 | grep -E "warp-wrap|MicroSOCKS|IPv4 ip"
# expect: MicroSOCKS listening, one "warp-wrap LAN return route" line, WARP egress IP
curl -s --socks5-hostname 127.0.0.1:1080 https://www.cloudflare.com/cdn-cgi/trace | grep warp=
# expect: warp=on
```

The app log should show
`[Init] Setting global proxy for bilibili_api: socks5://microwarp:1080`.
The app needs no HTTP hop: it speaks SOCKS5 natively (media via
`CdnConnection`, API via the libcurl transport), so the optional
`microwarp-http` gost bridge can be dropped.

## Why the wrapper image

Upstream installs WireGuard policy routing with table 128 holding only
`default via <gw>`. Replies from MicroSOCKS to same-subnet Docker peers
then hairpin through the bridge gateway, which the host drops as INVALID
conntrack (it never saw the direct-L2 SYN) — peer connections hang until
timeout. `entrypoint-wrap.sh` runs the stock entrypoint, waits for the
tunnel, then mirrors every connected LAN route into table 128, restoring
direct L2 replies. It re-runs on every start, so recreates and changing
subnets are handled. `microwarp` stays single-homed on `warp-net` on
purpose — a second attached network breaks the return path the same way.

## Troubleshooting

- `docker logs microwarp` refuses to start with a SOCKS auth error: set
  `ALLOW_NO_AUTH=1` (no-auth mode) or `SOCKS_USER`/`SOCKS_PASS` instead.
- App gets 500s / 15s timeouts to Bilibili: the tunnel is down or the
  `warp-wrap` line is missing from the microwarp log — `docker compose
  -f tools/warp/app/compose.yml up -d --force-recreate` and re-check.
- `host.docker.internal:1080` from a container fails: expected — the
  loopback binds answer only on 127.0.0.1. Containers must use
  `socks5://microwarp:1080` on `warp-net`.
- `view` API returns 412 both direct and proxied: IP-level Bilibili risk
  control on that endpoint, not a proxy defect.

## Teardown

```bash
docker compose -f compose.yml -f tools/warp/app/miku-warp.override.yml.example down
docker compose -f tools/warp/app/compose.yml down -v   # -v drops the WARP registration
```
