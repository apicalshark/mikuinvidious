# Troubleshooting

## Datacenter IP risk-controlled (412 / -352)

Symptoms: space pages show 0 videos, empty search, empty live directory. No errors
logged.

Cause: Bilibili risk-controls datacenter IPs. IP-level blocking, not an app bug.

Fix: set `proxy_url` under `[proxy]` (or `HTTP_PROXY`) to forward Bilibili traffic
through a SOCKS5/HTTP egress (e.g. Cloudflare WARP). Home broadband connects
directly.

Fallbacks are built in: `User.get_user_info` retries non-WBI
`/x/web-interface/card`, `get_videos` retries `recArchivesByKeywords`. Degraded
pages show a banner and return `X-Degraded`.

## Empty search results

Rule out risk control first. Other classic cause: **never send the `bili_ticket`
cookie to web APIs** — it triggers the `v_voucher` pre-check and empties results.
Only the CDN/DASH proxy uses the `x-bili-ticket` header.

## Mid-play stalls

CDN URLs are time-signed (~2 h). Expiry takes down the whole file, not mid-play.
Stalls are bad edge nodes. Refresh for a fresh signature, switch quality, or wait
for mirror failover.

## ERR_QUIC_PROTOCOL_ERROR

Disable HTTP/3 in Caddy — see [Reverse proxy](reverse-proxy).

## Live room shows offline

Confirm the uploader is actually live. Otherwise `getInfoByRoom` is likely risk
controlled: it needs WBI + browser TLS (curl_cffi Chrome impersonation), and
datacenter IPs still need WARP.

## Still stuck

`SITE_SHOW_UNSAFE_ERROR_RESPONSE=true` for the full error (turn it off after),
then report with the video ID / room number and logs.
