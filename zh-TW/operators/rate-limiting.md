# 速率限制

本系統提供基於 Redis sliding-window 的每 IP 速率限制，**預設關閉**，公開實例建議啟用。

```toml
[rate_limit]
enabled = true
# trusted_proxies = '172.18.0.0/16'
```

- `enabled`（`RATE_LIMIT_ENABLED`）：總開關。
- `trusted_proxies`（`TRUSTED_PROXIES`）：額外信任的代理 IP／CIDR。代理標頭只對 loopback／私有網路／名單內來源採信，其餘一律採用 TCP 對端 IP，可防範標頭偽造。
