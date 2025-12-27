# MikuInvidious

A free as in freedom frontend for Bilibili.

This is a fork of [0xacab.org/johnxina/mikuinvidious](https://0xacab.org/johnxina/mikuinvidious) with my personal preference plus ai slop.

## Documentation

Further technical details and architecture documentation can be found in the `doc/` directory:

- [Stability Fixes](doc/stability-fixes.md): Details on the live stream timeout and keep-alive strategy.
- [Keep-Alive Mechanism](doc/keep-alive.md): In-depth look at the FLV heartbeat system.
- [Live Logic](doc/live-logic.md): Overview of how live streaming is implemented.
- [Project Overview](GEMINI.md): General architecture and project philosophy.

## Quick Start (Docker)

The easiest way to run MikuInvidious is using Docker Compose.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/MikuInvidious/MikuInvidious.git
   cd MikuInvidious
   ```

2. **Run with Docker Compose:**
   ```bash
   docker compose up
   ```

The application will be available at `http://localhost:8000`.

### Configuration

You can customize the application by editing the `environment` section in `compose.yml` or by creating a `config.toml` file.
Full reference is in shared.py.

Key environment variables:
- `SITE_NAME`: The name of your instance.
- `SITE_URL`: The public URL of your instance.
- `HTTP_PROXY` / `HTTPS_PROXY`: SOCKS5 proxy (configured to use the included `warp` service by default).
- `REDIS_URL`: Connection string for Redis.
- `QUART_SECRET_KEY`: A random secret string for session security.
