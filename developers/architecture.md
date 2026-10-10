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

- **Caddy: reverse proxy + static files only.** App logic in Quart, async I/O.
- **Media proxy always on.** `CdnConnection` over raw sockets, direct or WARP
  SOCKS5. `ProxyResponse` + `ClosingIterator`: no fd leaks.
- **WARP optional.** Datacenter IPs bypassing risk control. Home broadband: direct.
- **Redis required.** Sessions, playurl cache (`miku_dash_*`, 1800 s), page cache.
- **3 h streaming timeouts.** `RESPONSE_TIMEOUT` / `BODY_TIMEOUT` fixed at
  10800 s. Long videos play to the end.
