# MikuInvidious Docs

A free and open-source frontend for Bilibili, inspired by
[Invidious](https://invidious.io). No account, no official app, no tracking.

- **Video** — DASH adaptive streaming, danmaku, subtitles, multi-part videos.
- **Audio-only mode** — any video as audio only.
- **Live** — forwarded streams with heartbeat keep-alive and real-time chat.
- **Bangumi** — episode browsing with optional Nyaa.si torrent search.
- **Articles & Opus** — Bilibili `cv` articles and `opus` posts as proxy pages.
- **Search** — videos, uploaders, articles, live rooms, bangumi, with filters.
- **Privacy** — all media is server-side proxied. Client IPs never reach
  Bilibili's CDN.

| Section | Audience |
| :--- | :--- |
| [Operator guide](operators/) | Deploying and running an instance. |
| [Developer guide](developers/) | Working on the app, player, themes, or API wrapper. |

- Run it: [Quickstart with Docker](operators/quickstart-docker) (default local
  address: `http://localhost:8000`).
- Understand it: [Architecture](developers/architecture).
- Source: [apicalshark/mikuinvidious](https://github.com/apicalshark/mikuinvidious)
  (GNU GPL-3.0).
