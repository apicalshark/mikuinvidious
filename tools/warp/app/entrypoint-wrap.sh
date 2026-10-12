#!/bin/sh
# Wrapper around upstream MicroWARP entrypoint.
#
# Upstream installs WireGuard policy routing with table 128 holding ONLY
# `default via <gw>`. Replies from MicroSOCKS to same-subnet docker peers
# then hairpin through the bridge gateway, which the host drops as
# INVALID conntrack (it never saw the direct-L2 SYN) — peer connections
# hang until timeout. Mirroring each connected LAN route into table 128
# restores direct L2 replies. Runs on every start (entrypoint), so it
# survives container recreates and changing subnets.
set -eu

/app/entrypoint.sh &
pid=$!

# Wait until upstream brings the tunnel/policy routing up (max 60s).
i=0
while [ "$i" -lt 60 ]; do
    if ip route show table 128 2>/dev/null | grep -q '^default '; then
        break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        break # upstream died early (e.g. bad auth) — exit with its code
    fi
    i=$((i + 1))
    sleep 1
done

if kill -0 "$pid" 2>/dev/null; then
    # Mirror connected (scope link) LAN routes into table 128. Skips the
    # default route, via-routes (e.g. tailscale restore), and wg0 itself.
    ip -4 route show table main 2>/dev/null \
        | awk '$2 == "dev" && $0 ~ /scope link/ {print $1, $3}' \
        | while read -r net dev; do
            if ip route replace "$net" dev "$dev" table 128 2>/dev/null; then
                echo "==> [warp-wrap] LAN return route: $net dev $dev table 128"
            fi
        done
fi

wait "$pid"
