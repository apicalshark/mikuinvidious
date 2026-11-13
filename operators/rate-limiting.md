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

Alternatively, configure via environment variables:

For Docker deployments, set them under the `app` service environment in `compose.yml`:

```yaml
environment:
  - RATE_LIMIT_ENABLED=true
  - TRUSTED_PROXIES=172.18.0.0/16
```

For direct CLI launches, export the environment variables before starting the server:

```bash
export RATE_LIMIT_ENABLED=true
export TRUSTED_PROXIES="172.18.0.0/16"
uv run python/main.py
```

> [!IMPORTANT]
> Values configured in `config.toml` take precedence over environment variables. If `config.toml` contains `[rate_limit] enabled = false`, it overrides `RATE_LIMIT_ENABLED=true`. To enable rate limiting when using environment variables, either remove the conflicting `enabled` setting from `config.toml` or set it to `true`.

---

## 2. Trusted Proxies & Client IP Detection

The rate limiter evaluates client IPs using `get_client_ip()` in `python/miku/core/rate_limit.py`:

1. **Direct Peer Check**: If the connecting peer does not match `trusted_proxies` (which defaults to loopback `127.0.0.1/8,::1/128`), the TCP remote IP is used directly. Private RFC 1918 networks are **not** implicitly trusted, and any forwarded headers (`X-Forwarded-For`, `X-Real-IP`) from untrusted peers are ignored to prevent identity spoofing.
2. **Trusted Proxy Forwarding**: If the connecting peer matches a CIDR in `trusted_proxies` (for example `172.30.0.0/24` in Docker Compose), the application parses `X-Forwarded-For` from right to left (stopping at the first untrusted IP, falling back to `X-Real-IP`).

> [!WARNING]
> **Reverse Proxy Trust**: Ensure direct access to the application's listening port (e.g., `8888` for bare-metal or `8080` in container networks) is firewalled, include your reverse proxy's IP/subnet in `TRUSTED_PROXIES` when running behind Docker/Caddy, and configure the edge proxy to overwrite/strip untrusted `X-Forwarded-For` headers.

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
