# Tool Proposal: Miku-Proxy-Rotator (SOCKS5 IPv6 Rotator)

## 1. Objective
Create a standalone SOCKS5 proxy server that automatically rotates outgoing IPv6 addresses from a predefined pool (e.g., Oracle VPS secondary IPs). This decouples rotation logic from the main application and provides a standard interface for any service.

## 2. Architecture
- **Input**: SOCKS5 interface (default port 1081).
- **Core Logic**: For every incoming connection, the proxy selects an outgoing local IP from the available pool.
- **Rotation Strategy**:
    - Random per connection.
    - Round-robin.
    - Periodic (e.g., every 5 minutes).
- **Auto-Discovery**: Ability to automatically detect available global IPv6 addresses on a specific interface.

## 3. Technology Stack Selection

### Option A: Go (Recommended)
- **Pros**:
    - High performance and low memory footprint (perfect sidecar).
    - Excellent networking libraries (`net`, `golang.org/x/net/proxy`).
    - Compiles to a single static binary.
- **Library**: `armon/go-socks5` or a custom implementation using standard `net` package.

### Option B: Python (Asyncio)
- **Pros**:
    - Consistent with the main project's language.
    - Rapid development.
- **Cons**: Higher resource usage compared to Go.
- **Library**: `aiosocksy` or `proxy.py`.

## 4. Proposed Features
- [ ] **IP Pool Management**: Static list via config or dynamic detection via `ip addr`.
- [ ] **Health Checks**: Verify IP connectivity to Bilibili before using it.
- [ ] **Logging**: Trace which IP is being used for which request.
- [ ] **Docker Integration**: Standalone `Dockerfile` and easy integration into `compose.yml`.

## 5. Deployment Example
In `compose.yml`:
```yaml
services:
  rotator:
    build: ./tools/miku-proxy-rotator
    network_mode: host # Required to bind to host IPs
    environment:
      - BIND_INTERFACE=eth0
      - ROTATION_STRATEGY=random

  app:
    environment:
      - HTTP_PROXY=socks5://127.0.0.1:1081
```

## 6. Next Steps
1. Decide on the implementation language (Go is suggested for performance).
2. Design the configuration schema (YAML/TOML).
3. Implement the core SOCKS5 forwarding logic with `local_addr` binding.
