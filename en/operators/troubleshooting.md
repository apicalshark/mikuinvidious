# Troubleshooting

## Datacenter IP under risk control (412 / -352)

**Symptoms**: space pages show 0 videos, search returns empty, live directory is
empty — but the app logs no errors.

**Cause**: Bilibili risk-controls datacenter IPs. This is IP-level blocking, not
an application bug.

**Fix**: set `proxy_url` under `[proxy]` (or `HTTP_PROXY`) to forward traffic to
Bilibili through a SOCKS5/HTTP egress (e.g. Cloudflare WARP). Home broadband
connections can connect directly without a proxy.

The fallbacks are built in — nothing to do by hand: `User.get_user_info` falls
back to non-WBI `/x/web-interface/card`, `get_videos` falls back to
`recArchivesByKeywords`. While degraded, pages show a banner and return an
`X-Degraded` header.

## Search always empty

Rule out risk control above first. The other classic cause: **never send the
`bili_ticket` cookie to web APIs** — it triggers the `v_voucher` pre-check and
empties the results. This system only uses the `x-bili-ticket` header on the
CDN/DASH proxy. Follow that rule and you'll never hit it.

## Playback stalls mid-video

CDN URLs are time-signed (about two hours). An expired signature shows up as the
whole content being unreachable — not as a mid-play stall. Stalls are usually bad
edge-node connectivity. Remedies: refresh for a fresh signature, switch quality,
or wait for the mirror failover to move to a healthy node.

## ERR_QUIC_PROTOCOL_ERROR

Disable HTTP/3 in Caddy — see [Reverse proxy](reverse-proxy).

## Live room shows "offline"

First confirm the uploader is actually live. If so, `getInfoByRoom` may be risk
controlled — that endpoint needs WBI signing plus browser TLS (implemented here
with curl_cffi Chrome impersonation), and datacenter IPs still need WARP.

## Still stuck

Enable `SITE_SHOW_UNSAFE_ERROR_RESPONSE=true` for the full error (turn it off
after debugging) and report with the video ID / room number and relevant logs.
