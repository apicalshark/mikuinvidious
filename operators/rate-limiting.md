# Rate Limiting & Anti-Abuse

MikuInvidious includes an atomic Redis sliding-window token bucket rate limiter to protect public instances from scraping floods and Denial of Service attacks.

Rate limiting is **disabled by default**. For public instances exposed directly to the internet, enabling it is strongly recommended.

---

## 1. Enabling the Rate Limiter

In `config.toml`:

```toml
[rate_limit]
enabled = true
trusted_proxies = "172.18.0.0/16, 10.0.0.0/8"
```

Or via environment variables:

```bash
RATE_LIMIT_ENABLED=true
TRUSTED_PROXIES="172.18.0.0/16"
```

---

## 2. Trusted Proxies & Client IP Detection

The rate limiter evaluates client IPs using `get_client_ip()` in `python/rate_limit.py`:

1. **Direct Peer Check**: If the connecting peer is outside private ranges and not listed in `trusted_proxies`, the TCP remote IP is used directly. Any forwarded headers (`X-Real-IP`, `X-Forwarded-For`) from untrusted peers are ignored to prevent identity spoofing.
2. **Trusted Proxy Forwarding**: If the connecting peer is loopback (`127.0.0.1`, `::1`), a private RFC 1918 address (such as Docker bridge `172.18.0.0/16`), or explicitly matches a CIDR in `trusted_proxies`, the application trusts `X-Real-IP` and `X-Forwarded-For`.

> [!WARNING]
> **Implicit Private Forwarder Trust**: Because loopback and private networks are trusted implicitly, you must ensure that direct access to port 8080 is firewalled and that your reverse proxy (Caddy) always overwrites `X-Real-IP` with the validated client remote address.

---

## 3. Rate Limit Profiles

Predefined bucket profiles are applied based on endpoint sensitivity:

| Profile | Limit | Window | Applied Endpoints |
| :--- | :--- | :--- | :--- |
| **Strict** | 10 req | 60 sec | Sensitive administrative endpoints and raw credential actions. |
| **Search** | 20 req | 60 sec | Category and keyword search endpoints (`/search`). |
| **Normal** | 60 req | 60 sec | General video metadata, user spaces, and Bangumi views. |
| **Proxy** | 100 req | 60 sec | Media streaming fragments and image proxies. |
| **Loose** | 300 req | 60 sec | Auxiliary static assets and auxiliary resources. |

When a client exceeds the quota, the server responds with `HTTP 429 Too Many Requests` and a `Retry-After` header. Allowed responses include standard telemetry headers:
- `X-RateLimit-Limit`: Maximum requests permitted in the window.
- `X-RateLimit-Remaining`: Remaining request allowance in current window.
- `X-RateLimit-Reset`: UNIX epoch timestamp when the current window expires.
