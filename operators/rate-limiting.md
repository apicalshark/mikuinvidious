# Rate limiting

Redis sliding-window per-IP rate limiting. **Off by default**; public instances
should enable it.

```toml
[rate_limit]
enabled = true
# trusted_proxies = '172.18.0.0/16'
```

- `enabled` (`RATE_LIMIT_ENABLED`): master switch.
- `trusted_proxies` (`TRUSTED_PROXIES`): extra trusted proxy IPs/CIDRs. Proxy
  headers are honored only from loopback / private networks / listed sources;
  everything else uses the TCP peer IP, which defeats header forgery.
