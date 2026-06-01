# Miku-Proxy-Rotator

A high-performance SOCKS5 proxy server written in Go that rotates outgoing IPv6 addresses. Designed as a sidecar for MikuInvidious to mitigate Bilibili rate-limiting.

## Features
- **IP Rotation**: Multiple strategies to cycle outgoing IPs.
- **Auto-Discovery**: Can automatically detect all global IPv4/IPv6 addresses on a specified network interface.
- **Dual-Stack Support**: Smartly handles IPv4 and IPv6 target addresses using corresponding local IP pools.
- **Lightweight**: Built with Go, minimal resource footprint.
- **Decoupled**: Works as a standalone SOCKS5 proxy.

## Rotation Strategies
Set `ROTATION_STRATEGY` environment variable:
- `random` (default): Picks a random IP from the pool for every new connection.
- `round-robin`: Cycles through the IP pool in order.
- `periodic`: Uses one IP for a certain period, then switches. Configure duration with `ROTATION_INTERVAL` (e.g., `10m`, `1h`).

## How to use with MikuInvidious

### 1. Update `compose.yml`
Add the rotator service and configure the main `app` to use it as a proxy.

```yaml
services:
  rotator:
    build: ./tools/miku-proxy-rotator
    container_name: miku_rotator
    network_mode: host # Crucial for binding to host IPv6 addresses
    restart: always
    environment:
      - BIND_INTERFACE=eth0 # The interface where your IPv6 addresses are
      - ROTATION_STRATEGY=round-robin
      # - ROTATION_INTERVAL=5m (Only for periodic)
    restart: always
```

### 2. Environment Variables
- `PORT`: SOCKS5 port to listen on (default: `1081`).
- `BIND_INTERFACE`: Automatically find Global IPv4/IPv6 addresses on this interface.
- `BIND_IPS`: A comma-separated list of specific IPs to use.
- `SKIP_IPS`: A comma-separated list of IPs to exclude from the rotation pool (e.g., exclude the primary host IP).
- `ROTATION_STRATEGY`: `random`, `round-robin`, or `periodic`.
- `ROTATION_INTERVAL`: Duration for `periodic` strategy (default: `5m`).

## Technical Details
The rotator uses the `local_address` binding feature of TCP/UDP connections. Each time a client connects to the SOCKS5 server, the server picks a random IP from the pool and binds the outgoing connection to it.
