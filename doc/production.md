# Production Deployment with Docker

This guide covers deploying MikuInvidious in a production environment using Docker Compose and Caddy.

## Prerequisites

- **Docker** and **Docker Compose** installed.
- A domain name (if using HTTPS).
- Ports **80**, **443 (TCP/UDP)** open on your firewall.

---

## 1. Quick Start (Production)

1. **Clone the repository:**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Configure `compose.yml`:**
   Update the `SITE_URL` in the `app` service to your domain:

   ```yaml
   environment:
     - SITE_URL=https://yourdomain.com
   ```

3. **Configure `Caddyfile`:**
   Change the first line to your domain name:

   ```caddy
   yourdomain.com {
       handle /static/* {
           root * /usr/share/caddy
           file_server
       }
       reverse_proxy app:8080
   }
   ```

4. **Launch:**

   ```bash
   docker compose up -d
   ```

---

## 2. SSL Management

Caddy handles SSL/TLS certificates automatically by default.

### Automatic SSL (Recommended)

Caddy will automatically obtain and renew certificates from Let's Encrypt or ZeroSSL.

- **Configuration:** Simply use your domain name as the site address in the `Caddyfile`.
- **Auto-Redirect:** Caddy automatically redirects all HTTP traffic (port 80) to HTTPS (port 443).

### Manual Certificates

If you already have certificates (e.g., Cloudflare Origin CA), follow these steps:

1. **Place your certificates** in the `ssl/` directory (e.g., `ssl/cert.pem` and `ssl/key.pem`).
2. **Update `compose.yml`** to mount the SSL directory:

   ```yaml
   caddy:
     volumes:
       - ./ssl:/etc/caddy/ssl:ro
       # ... other volumes
   ```

3. **Update `Caddyfile`** to use your certificates:

   ```caddy
   yourdomain.com {
       tls /etc/caddy/ssl/cert.pem /etc/caddy/ssl/key.pem
       # ... other config
   }
   ```

---

## 3. HTTP/3 (QUIC) Support

Caddy supports HTTP/3 out of the box. Ensure that port **443/UDP** is open in your firewall and correctly mapped in `compose.yml`.

---

## 4. Maintenance

- **View Logs:** `docker compose logs -f caddy`
- **Update Application:**

  ```bash
  git pull
  docker compose up -d --build
  ```
