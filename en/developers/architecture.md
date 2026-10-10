# Architecture

```mermaid
graph TD
    User((user))
    Caddy["Caddy reverse proxy<br>(8000)"]
    Granian["Granian ASGI<br>(8080)"]
    Router{router}
    Proxy["media proxy<br>(proxy.py / live_manager.py)"]
    Bangumi["bangumi<br>(views_bangumi.py)"]
    Views["main views<br>(views.py)"]
    BiliAPI["Bilibili API wrapper"]
    Redis[("Redis")]
    Warp["WARP SOCKS5<br>(optional)"]
    BiliCDN["Bilibili CDN"]
    BiliSrv["Bilibili API"]
    Nyaa["Nyaa.si"]

    User --> Caddy
    Caddy -- "static files" --> Granian
    Caddy -- "app traffic" --> Granian
    Granian --> Router
    Router -- "/proxy/..." --> Proxy
    Router -- "/bangumi/..." --> Bangumi
    Router -- "other" --> Views
    Proxy --> Redis
    Views --> BiliAPI
    Bangumi --> BiliAPI
    BiliAPI --> Warp
    Proxy --> Warp
    Warp --> BiliCDN
    Warp --> BiliSrv
    Bangumi --> Nyaa
```

## Key design decisions

- **Caddy only reverse-proxies and serves static files**. Application logic lives in
  Quart, all async I/O.
- **The media proxy is always on**: `CdnConnection` dials over raw sockets, direct
  or via WARP SOCKS5. `ProxyResponse` + `ClosingIterator` guarantee no fd leaks.
- **WARP is optional**: for datacenter IPs to bypass risk control. Home broadband
  connects directly.
- **Redis is required**: sessions, playurl cache (`miku_dash_*`, 1800s), and page
  cache all depend on it.
- **3-hour streaming timeouts**: `RESPONSE_TIMEOUT` / `BODY_TIMEOUT` are fixed at
  10800 seconds so long videos play to the end.
