# Quickstart with Docker

The recommended and easiest method to deploy MikuInvidious. Caddy, Granian, Redis, and disk caching volumes are configured out-of-the-box.

---

## 1. Prerequisites

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- Firewall ports `80` (TCP), `443` (TCP/UDP), and `8000` (optional local testing) open.

---

## 2. Fast Deployment

1. **Clone the repository**:

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Prepare Caddy configuration**:

   ```bash
   cp Caddyfile.example Caddyfile
   ```

3. **Start the containers**:

   ```bash
   docker compose up -d
   ```

Your instance will immediately be available at `http://localhost:8000`.

---

## 3. Configuring a Custom Public Domain

1. Open `compose.yml`:
   - Set the public domain in the `app` service environment (note that the modern theme header displays the "MikuInvidious" brand mark regardless of `SITE_NAME`):

     ```yaml
     environment:
       - SITE_NAME=My MikuInvidious Instance
       - SITE_URL=https://miku.example.com
     ```

   - Publish port `80:80` on the `caddy` service so Caddy can complete HTTP-01 ACME challenges and perform HTTP-to-HTTPS redirects:

     ```yaml
     caddy:
       ports:
         - "80:80"
         - "443:443/tcp"
         - "443:443/udp"
         - "8000:8000"
     ```

2. In `Caddyfile`, replace the `:8000` site address with your actual domain. Preserve the preceding global options block:

   ```text
   miku.example.com {
       root * /usr/share/caddy

       handle /static/* {
           file_server
       }
       handle /site.webmanifest {
           root * /usr/share/caddy/static
           header Content-Type "application/manifest+json"
           file_server
       }

       @notProxy not path /proxy/*
       encode @notProxy zstd gzip

       handle {
           reverse_proxy app:8080 {
               header_up X-Real-IP {remote_host}
           }
       }
   }
   ```

3. Restart the stack:

   ```bash
   docker compose up -d --force-recreate
   ```

Caddy will automatically request and renew trusted SSL/TLS certificates from Let's Encrypt or ZeroSSL.

---

## 4. Datacenter IP Note

If hosting on cloud providers (Hetzner, OVH, DigitalOcean, AWS), Bilibili may challenge or block server IPs with HTTP 412 or `-352` errors. WARP is not enabled by default in the root `compose.yml`; to route outbound requests through Cloudflare WARP, follow the instructions in `tools/warp/` to launch the optional companion stack, or configure an external residential/SOCKS5 proxy via `HTTP_PROXY` and `HTTPS_PROXY` in `compose.yml`.
