# Quickstart with Docker

Docker is the officially recommended deployment. Redis and Caddy come preconfigured.

## Prerequisites

- Docker and Docker Compose installed.
- A domain name (only needed for HTTPS); firewall must allow ports 80 and 443 (TCP/UDP).

## Start

```bash
git clone https://github.com/apicalshark/mikuinvidious
cd mikuinvidious
cp Caddyfile.example Caddyfile
docker compose up -d
```

Open `http://localhost:8000`. If the homepage renders, deployment succeeded.

## Bind a domain

1. Change `SITE_URL` of the `app` service in `compose.yml` to your domain
   (e.g. `https://mi.example.com`).
2. Change the first line of `Caddyfile` to that domain:

```text
mi.example.com {
    handle /static/* {
        root * /usr/share/caddy
        file_server
    }
    reverse_proxy app:8080
}
```

3. Run `docker compose up -d` to restart. Caddy will automatically obtain and
   renew certificates from Let's Encrypt / ZeroSSL.

## Note for datacenter IPs

Hosts in datacenters (e.g. Hetzner, OVH) get risk-controlled by Bilibili
(HTTP 412, `-352`), leaving space pages and search results empty. In that case
configure a WARP egress — see [Troubleshooting](troubleshooting). Home broadband
connections can connect directly, no proxy needed.
