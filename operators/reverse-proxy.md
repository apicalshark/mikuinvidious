# Reverse proxy (Caddy)

Caddy terminates HTTPS, serves static files, forwards the rest to `app:8080`.

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

`encode` skips `/proxy/*`: streams are already compressed; re-compressing wastes CPU.

## `ERR_QUIC_PROTOCOL_ERROR` during playback

Browser QUIC stacks mishandle 206 Partial Content. Disable HTTP/3 at the top of
`Caddyfile`:

```text
{
    servers {
        protocols h1 h2
    }
}
```

Restart Caddy. Known issue, not a network problem.

## Own certificate

```text
mi.example.com {
    tls /path/to/cert.pem /path/to/key.pem
    ...
}
```

Otherwise Caddy provisions from Let's Encrypt / ZeroSSL with no configuration.
