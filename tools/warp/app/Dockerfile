# MicroWARP + LAN-return-route wrapper (see entrypoint-wrap.sh).
# Upstream policy routing (table 128 = default-only) drops replies to
# same-subnet docker peers; the wrapper mirrors connected LAN routes into
# table 128 after the tunnel is up. Rebuild after pulling a new upstream:
#   docker compose build --pull
FROM ghcr.io/ccbkkb/microwarp:latest
COPY entrypoint-wrap.sh /app/entrypoint-wrap.sh
ENTRYPOINT ["/app/entrypoint-wrap.sh"]
