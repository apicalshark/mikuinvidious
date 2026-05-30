# MikuInvidious

A free as in freedom frontend for Bilibili.

This is a fork of [0xacab.org/johnxina/mikuinvidious](https://0xacab.org/johnxina/mikuinvidious) with my personal preference plus ai slop.

## Application Features

### 📺 Video & Media Playback

- **High-Quality Streaming**: Support for DASH and FLV formats
- **Danmaku Overlay**: Native implementation of scrolling comments (danmaku) for the full Bilibili experience.
- **Listen Mode**: Save bandwidth and focus on the audio with a dedicated audio-only interface for any video.
- **Audio Posts**: Full support for Bilibili Audio (au) and Playlists (am) with specialized player controls.
- **Multi-Part Videos**: Seamless navigation through multi-page video series.

### 🔴 Enhanced Live Streaming

- **Stability-First Proxying**: Custom ASGI-based proxying that prevents the common 60-second idle drops found in standard proxies.
- **Live Chat**: Real-time SSE (Server-Sent Events) chat integration, allowing you to follow the conversation without official trackers.

### 📖 Content Discovery & Reading

- **Distraction-Free Articles**: Clean, proxied rendering of Bilibili Articles (cv) and Opus (dynamic posts).
- **Global Search**: Search across videos, live rooms, users, and articles with advanced sorting filters.
- **User Spaces**: Explore user profiles, their video uploads, and article contributions.
- **Category Browsing**: Detailed category (Zone) views with the latest content.

### 🛡️ Privacy & Security

- **No-Account Browsing**: Full access to Bilibili content without needing to log in or maintain a Bilibili account.
- **Media Proxying**: Proxies images and (optional) video streams through the server and Cloudflare WARP to mask your IP address.
- **Zero Tracking**: Strips away official Bilibili tracking scripts and telemetry.
- **Local History**: Privacy-respecting browsing history stored locally in your browser and your instance's Redis cache.

## Quick Start (Docker)

The easiest way to run MikuInvidious is using Docker Compose.

1. **Clone the repository:**

   ```bash
   git clone https://github.com/apicalshark/mikuinvidious
   cd mikuinvidious
   ```

2. **Run with Docker Compose:**

   ```bash
   mv Caddyfile.example Caddyfile
   docker compose up -d
   ```

The application will be available at `http://localhost:8000`.

### Local Installation (Without Docker)

For users who want to run the entire application without Docker, please see the [detailed local installation guide](doc/setup.md). This guide requires manual installation and configuration of Redis and the Cloudflare Warp client.

### Configuration

You can customize the application by editing the `environment` section in `compose.yml` or by creating a `config.toml` file.
Full reference is in shared.py.

Key environment variables:

- `SITE_NAME`: The name of your instance.
- `SITE_URL`: The public URL of your instance.
- `HTTP_PROXY` / `HTTPS_PROXY`: SOCKS5 proxy (configured to use the included `warp` service by default).
- `REDIS_URL`: Connection string for Redis.
- `QUART_SECRET_KEY`: A random secret string for session security.

Additionally, you can use the manage_quic.sh to simplify SSL credential and QUIC setup.

## License

MikuInvidious is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 3 of the License, or (at your option) any later version.

### JavaScript Licenses

The following free software JavaScript libraries are included in this project:

| Library | License | Source |
| :--- | :--- | :--- |
| **hls.js** | Apache-2.0 | [github.com/video-dev/hls.js](https://github.com/video-dev/hls.js) |
| **mpegts.js** | Apache-2.0 | [github.com/xqq/mpegts.js](https://github.com/xqq/mpegts.js) |
| **dash.js** | BSD-3-Clause | [github.com/Dash-Industry-Forum/dash.js](https://github.com/Dash-Industry-Forum/dash.js) |
| **media-chrome** | MIT | [github.com/muxinc/media-chrome](https://github.com/muxinc/media-chrome) |
| **Danmaku** | MIT | [github.com/weizhenye/Danmaku](https://github.com/weizhenye/Danmaku) |
| **opencc-js** | MIT | [github.com/nk2028/opencc-js](https://github.com/nk2028/opencc-js) |
