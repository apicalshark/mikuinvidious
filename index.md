# MikuInvidious Docs

A free and open-source frontend for Bilibili, inspired by [Invidious](https://invidious.io). No account, no official app, zero tracking.

---

## Key Features

- **Media Playback** — Dynamic ISO/IEC 23009-1 DASH adaptive streaming, multi-mirror failover, danmaku overlay, subtitles, and multi-part videos.
- **Universal Search Bar** — Paste tracking links (`b23.tv`), official web/app URLs, or IDs (`BV...`, `ep...`, `cv...`) to instantly jump to clean, telemetry-free content.
- **Listen Mode (`?listen=1`)** — High-definition audio-only streaming for podcasts and music; saves bandwidth and battery.
- **Background Downloads** — Resilient multi-track chunk downloading with server-side `ffmpeg` faststart MP4 muxing.
- **Live Streams** — Low-latency FLV and HLS stream forwarding with heartbeat keep-alive and real-time danmaku chat.
- **Anime & Bangumi** — Series catalog and episode browser with optional Nyaa.si torrent integration.
- **Articles & Opus** — Clean, distraction-free readers for Bilibili `cv` articles and dynamic `opus` posts with proxied images.
- **Privacy by Default** — All video, audio, and images are server-side proxied with local disk caching. Your IP never connects to Bilibili's CDN.

---

## Documentation Sections

| Section | Target Audience | Key Contents |
| :--- | :--- | :--- |
| **[User guide](guide/)** | End users & viewers | Bilibili link resolving, search bar tips, URL navigation, player shortcuts, and downloads. |
| **[Operator guide](operators/)** | Instance administrators | Deploying with Docker, Caddy TLS, Redis, configuration reference, and Anubis bot defense. |
| **[Developer guide](developers/)** | Developers & contributors | Python 3.15+ architecture, modular DASH engine, Bilibili API wrapper, and test verification. |
| **[Public instances](instances)** | Everyone | List of community-maintained public instances. |

---

## Quick Links

- **Get Started**: [Quickstart with Docker](operators/quickstart-docker) (default local address: `http://localhost:8000`).
- **Understand System Architecture**: [System architecture](developers/architecture).
- **Source Code**: [apicalshark/mikuinvidious](https://github.com/apicalshark/mikuinvidious) (GNU AGPL-3.0).
