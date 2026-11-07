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

    # Serve static assets directly from disk (bypasses Python app entirely)
    handle /static/* {
        header X-Content-Type-Options "nosniff"
        header Cache-Control "public, no-cache"
        file_server
    }

    handle /site.webmanifest {
        root * /usr/share/caddy/static
        header Content-Type "application/manifest+json"
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
> ```text
> servers {
>     protocols h1 h2
>     trusted_proxies static <upstream_proxy_cidrs>
>     client_ip_headers CF-Connecting-IP X-Forwarded-For
> }
> ```
> Caddy will validate that incoming requests arrive from trusted proxy CIDRs before reading the specified client IP headers to populate `{client_ip}`, preventing spoofing from direct clients while preserving the visitor's real IP and `{scheme}`.

---

## 3. High-Traffic Bot Defense with Anubis PoW

For public instances experiencing aggressive scraper traffic or volumetric abuse, MikuInvidious includes an Anubis Proof-of-Work (PoW) configuration (`tools/botPolicy.yaml.example` and `tools/Caddyfile.prod.example`).

```
[ Browser / Scraper ]
         |
         v
    [ Caddy ]
         | (Static /static/* served directly)
         v
    [ Anubis PoW Gateway ] -- Challenges scrapers / headless bots
         |
         v (Verified Browsers)
    [ Granian / Quart ]
```

- **Static Bypass**: `/static/*` and infra health checks bypass challenges completely.
- **Image Proxy Route (`/proxy/pic/*`)**: Shielded by Anubis PoW challenges; behind Anubis, Python serves cached images from local disk in `< 1ms`.
- **Targeted Bot Policies**: Drops vulnerability probes (`.env`, `.git`, `.php`), denies AI scraper User-Agents, and challenges datacenter / commercial VPN ASNs with lightweight client-side PoW challenges before they reach Python.
