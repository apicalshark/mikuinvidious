# Reverse Proxy (Caddy)

Caddy serves as the public entry point for MikuInvidious: it handles automatic TLS certificate provisioning, serves static assets directly from disk, compresses text payloads, and forwards application traffic to Granian (`app:8080`).

---

## 1. Standard Production Caddyfile

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:c3300b5d15c8fa938179a3f7e5ac4c1c09fcc769383f54d5fbb859490bd38bf1 -->

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
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
```

<!-- /coderabbit-ai-generated:v1 -->

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
Granian runs behind Caddy. To ensure the rate limiter and audit logs receive accurate visitor IP addresses instead of Caddy's internal Docker IP, Caddy must pass:

```text
reverse_proxy app:8080 {
    header_up X-Real-IP {remote_host}
}
```

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
