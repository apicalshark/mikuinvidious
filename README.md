# MikuInvidious

A free as in freedom frontend for Bilibili.

This is a fork of [0xacab.org/johnxina/mikuinvidious](https://0xacab.org/johnxina/mikuinvidious) with my personal preference plus ai slop.

## Application Features

- **Media Playback**: Support for DASH/FLV, danmaku, and multi-part videos.
- **Listen Mode**: Bandwidth-saving audio-only interface for any video.
- **Live Streaming**: Stable proxying with heartbeats and real-time SSE chat.
- **Content Discovery**: Proxied articles (cv/opus) and global search with filters.
- **Privacy**: No-account browsing, IP masking via media proxying, and zero tracking.

## Quick Start (Docker)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Run with Docker Compose:**
   ```bash
   cp Caddyfile.example Caddyfile
   docker compose up -d
   ```
The application will be available at `http://localhost:8000`.

### Local Installation (Without Docker)

For users who want to run the application manually, see the [local installation guide](doc/setup.md). This project uses `uv` for dependency management.

## Tech Stack

- **Backend**: Python 3.14+, Quart (ASGI)
- **Server**: Granian (Rust-powered), Caddy
- **Cache**: Redis (required)
- **Frontend**: Tailwind CSS (Modern theme)

## Configuration

Settings can be customized in `compose.yml` or `config.toml`. Key variables:
- `SITE_NAME`: Instance name.
- `SITE_URL`: Public URL.
- `REDIS_URL`: Redis connection string.
- `HTTP_PROXY` / `HTTPS_PROXY`: SOCKS5 proxy (uses included `warp` service by default).

## License

MikuInvidious is licensed under the **GNU General Public License v3.0**.

### JavaScript Libraries
| Library | License | Source |
| :--- | :--- | :--- |
| **hls.js** | Apache-2.0 | [github.com/video-dev/hls.js](https://github.com/video-dev/hls.js) |
| **mpegts.js** | Apache-2.0 | [github.com/xqq/mpegts.js](https://github.com/xqq/mpegts.js) |
| **dash.js** | BSD-3-Clause | [github.com/Dash-Industry-Forum/dash.js](https://github.com/Dash-Industry-Forum/dash.js) |
| **media-chrome** | MIT | [github.com/muxinc/media-chrome](https://github.com/muxinc/media-chrome) |
| **Danmaku** | MIT | [github.com/weizhenye/Danmaku](https://github.com/weizhenye/Danmaku) |
| **opencc-js** | MIT | [github.com/nk2028/opencc-js](https://github.com/nk2028/opencc-js) |
