# Quickstart with Docker

Recommended deployment method. Redis and Caddy are preconfigured.

Prerequisites: Docker, Docker Compose, a domain name for HTTPS, firewall ports
80 and 443 (TCP/UDP) open.

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
cp Caddyfile.example Caddyfile
docker compose up -d
```

Open `http://localhost:8000`.

## Custom domain

1. Set `SITE_URL` of the `app` service in `compose.yml`
   (e.g. `https://mi.example.com`).
2. Set the first line of `Caddyfile` to the domain:

```text
mi.example.com {
    handle /static/* {
        root * /usr/share/caddy
        file_server
    }
    reverse_proxy app:8080
}
```

3. `docker compose up -d`. Caddy obtains and renews certificates from
   Let's Encrypt / ZeroSSL automatically.

## Datacenter IPs

Datacenter hosts (Hetzner, OVH, …) are risk-controlled by Bilibili (HTTP 412,
`-352`): space pages and search come back empty. Point `[proxy]` at a WARP
egress — see [Troubleshooting](troubleshooting). Home broadband connects
directly.
