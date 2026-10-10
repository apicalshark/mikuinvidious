# Rate limiting

Redis sliding-window, per IP. **Off by default.** Public instances should enable it.

```toml
[rate_limit]
enabled = true
# trusted_proxies = '172.18.0.0/16'
```

- `enabled` (`RATE_LIMIT_ENABLED`): master switch.
- `trusted_proxies` (`TRUSTED_PROXIES`): extra trusted proxy IPs/CIDRs. Proxy
  headers are honored from loopback / private / listed sources only; everything
  else uses the TCP peer IP. Forged headers are ignored.
