# Reverse Proxy (Caddy)

Caddy serves as the public entry point for MikuInvidious: it handles automatic TLS certificate provisioning, serves static assets directly from disk, compresses text payloads, and forwards application traffic to Granian (`app:8080`).

---

## 1. Standard Production Caddyfile

```text
{
    # Disable HTTP/3 (QUIC) due to ERR_QUIC_PROTOCOL_ERROR on 206 Partial Content range requests
    servers {
        protocols h1 h2
    }
}

:8000 {
    # Set webroot for static assets
    root * /usr/share/caddy

    log {
        output stdout
        format json
    }

    request_body {
        max_size 1MB
    }

    header -Server

    # Serve static assets directly from disk (bypasses Python app entirely)
    handle /static/* {
        header X-Content-Type-Options "nosniff"
        header X-Frame-Options "DENY"
        header Referrer-Policy "strict-origin-when-cross-origin"
        header Permissions-Policy "camera=(), microphone=(), geolocation=()"
        header Cross-Origin-Resource-Policy "same-origin"
        file_server
    }

    handle /site.webmanifest {
        root * /usr/share/caddy/static
        header Content-Type "application/manifest+json"
        header X-Content-Type-Options "nosniff"
        header Cross-Origin-Resource-Policy "same-origin"
        file_server
    }

    # Compress HTML, JSON, and text responses (never re-compress media streams)
    @notProxy not path /proxy/*
    encode @notProxy zstd gzip

    # Forward all dynamic traffic to the application
    handle {
        reverse_proxy app:8080 {
            header_up X-Real-IP {client_ip}
            header_up X-Forwarded-For {client_ip}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
```

---

## 2. Critical Configuration Gotchas

### Disabling HTTP/3 (QUIC)

Modern browser QUIC implementations frequently encounter `ERR_QUIC_PROTOCOL_ERROR` when streaming segmented media via `206 Partial Content`. Always include:

```text
{
    servers {
        protocols h1 h2
    }
}
```

### Compression Exclusion (`@notProxy`)

Media streams (`/proxy/*`) are already compressed video/audio payloads. Re-compressing them wastes CPU cycles and induces latency. Ensure `encode` targets `@notProxy not path /proxy/*`.

### Client IP Forwarding

Granian runs behind Caddy. To ensure the rate limiter and audit logs receive accurate visitor IP addresses instead of Caddy's internal Docker IP, Caddy must pass the client IP and scheme:

```text
reverse_proxy app:8080 {
    header_up X-Real-IP {client_ip}
    header_up X-Forwarded-For {client_ip}
    header_up X-Forwarded-Proto {scheme}
}
```

> [!WARNING]
> Do not use `{remote_host}` if Caddy sits behind an upstream proxy, CDN, or load balancer (such as Cloudflare or AWS ALB). Under those conditions, `{remote_host}` evaluates to the IP of the upstream proxy itself rather than the visitor, causing all external visitors to pool into a single rate-limiting bucket.
>
> To handle upstream proxies properly, configure Caddy's global `servers` block with `trusted_proxies` containing only the upstream proxy CIDRs, and specify the headers to inspect:
>
> ```text
> servers {
>     protocols h1 h2
>     trusted_proxies static <upstream_proxy_cidrs>
>     client_ip_headers CF-Connecting-IP X-Forwarded-For
> }
> ```
>
> Caddy will validate that incoming requests arrive from trusted proxy CIDRs before reading the specified client IP headers to populate `{client_ip}`, preventing spoofing from direct clients while preserving the visitor's real IP and `{scheme}`.

---

## 3. High-Traffic Bot Defense with Anubis PoW

For public instances experiencing aggressive scraper traffic or volumetric abuse, MikuInvidious includes a production Anubis Proof-of-Work (PoW) stack in `example/docker/` (`compose.yml.example`, `Caddyfile.prod.example`, and `botPolicy.yaml.example`).

```
[ Browser / Scraper ]
         |
         v
    [ Caddy ]  (TLS, security headers, compression exclusion for /proxy/*)
         | (Static /static/* & /site.webmanifest bypass Anubis -> app:8080)
         v
    [ Anubis PoW Gateway ] -- Challenges scrapers / headless bots (:8923)
         |
         v (Verified Browsers)
    [ Granian / Quart ]  --> [ Redis ] / [ microwarp SOCKS5 ]
```

- **Static Bypass**: `/static/*` and `/site.webmanifest` bypass Anubis directly to `app:8080` with `Permissions-Policy`, `Cross-Origin-Resource-Policy: same-origin`, and `Cache-Control: public, max-age=86400`.
- **Site-Wide Security Headers**: `Caddyfile.prod.example` strips `Server`/`Via` and applies `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy` across normal responses as well as `handle_errors` error responses.
- **Image Proxy Route (`/proxy/pic/*`)**: Shielded by Anubis PoW challenges; behind Anubis, Python serves cached images from local disk in `< 1ms`.
- **Targeted Bot Policies (`botPolicy.yaml.example`)**: Drops unsupported HTTP methods, vulnerability probes (`.env`, `.git`, `.php`), and AI/SEO scrapers; challenges scripting libraries and headless frameworks; scores datacenter/VPN ASNs (`+15`) and targeted GeoIP countries (`+10`); gates `/space/*/json` for datacenter/targeted geos while allowing clean residential RSS/script access; sheds load at `load_1m >= 2.0`; and applies tiered PoW difficulty (`4` to `10`).

---

## 4. Public Instance Deployment (`example/docker/`)

The `example/docker/` directory provides a complete, hardened Docker Compose template for running a public MikuInvidious instance with Caddy, Anubis, Redis, and `microwarp` (Cloudflare WARP SOCKS5 egress).

### Setup Steps

1. **Copy the example files** into your deployment directory:

   ```bash
   cp example/docker/compose.yml.example compose.yml
   cp example/docker/Caddyfile.prod.example Caddyfile
   cp example/docker/botPolicy.yaml.example botPolicy.yaml
   ```

2. **Provide GeoIP databases** (required by `botPolicy.yaml` for `geoip` and `asns` rules):

   Place `GeoLite2-ASN.mmdb` and `GeoLite2-Country.mmdb` in `./data/geoip/` (mounted read-only to `/var/lib/GeoIP` in `anubis`), or uncomment `geoip.auto_update` in `botPolicy.yaml` with your MaxMind account ID and `MAXMIND_LICENSE_KEY`.

3. **Configure secrets and instance environment** in `.env`:

   ```bash
   QUART_SECRET_KEY=<random-64-hex>
   REDIS_PASSWORD=<strong-redis-password>
   ```

   In `compose.yml`:
   - Set `SITE_URL` (e.g., `https://miku.example.com`) on `app`, and optionally configure Bilibili credentials (`USE_CRED=true`, `SESSDATA`, `BILI_JCT`, `BUVID3`, `BUVID4`, `DEDEUSERID`).
   - Set `TARGET_HOST` on `anubis` to your public domain (e.g., `miku.example.com`). Keep `COOKIE_DYNAMIC_DOMAIN=false` so Anubis issues a host-only PoW cookie instead of a wildcard domain cookie.
   - Keep `TRUSTED_PROXIES=127.0.0.1/8,::1/128,172.30.0.0/24` aligned with the `miku_net` subnet (`172.30.0.0/24`).

4. **Configure `Caddyfile` for your domain**:
   - Replace `yourdomain` with your public domain name and set `email` to your ACME contact address.
   - If using standard Let's Encrypt instead of a custom ACME CA with External Account Binding, remove `acme_ca` and `acme_eab` from the global options block.
   - Ensure the `/var/log/caddy` host directory exists and is writable by the Caddy container.

5. **Start the public stack**:

   ```bash
   docker compose up -d
   ```

> [!NOTE]
> `example/docker/compose.yml.example` configures `redis` as an ephemeral `tmpfs` cache (`40mb`, `allkeys-lru`, persistence disabled) and routes `app` outbound traffic through `microwarp:1080` (`HTTP_PROXY` / `HTTPS_PROXY`). If your server uses a clean residential IP and does not need WARP, remove the `microwarp` service and proxy environment variables.

