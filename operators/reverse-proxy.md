# Reverse proxy (Caddy)

Caddy is the entry point users actually connect to: it terminates HTTPS, serves
static files, and forwards everything else to `app:8080`.

## Minimal working config

```text
:8000 {
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
        reverse_proxy app:8080
    }
}
```

Note that `encode` excludes `/proxy/*` — media streams are already-compressed
binary; re-compressing them only burns CPU.

## Disable HTTP/3 when playback breaks

`ERR_QUIC_PROTOCOL_ERROR` during playback is usually a browser QUIC-stack issue
with 206 Partial Content. Add this to the top of `Caddyfile`:

```text
{
    servers {
        protocols h1 h2
    }
}
```

Restart Caddy and it takes effect. This is a known issue, unrelated to the
user's network quality.

## Bring your own certificate

```text
mi.example.com {
    tls /path/to/cert.pem /path/to/key.pem
    ...
}
```

Without special needs, let Caddy obtain certificates automatically
(Let's Encrypt / ZeroSSL) — no extra configuration required.
