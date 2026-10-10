# What is MikuInvidious?

[MikuInvidious](https://github.com/apicalshark/mikuinvidious) is a free and open-source frontend for Bilibili, inspired by
[Invidious](https://invidious.io). It provides a lightweight, privacy-focused
interface for browsing and watching Bilibili content without an account and tracking.

## Features

- **Video** — DASH adaptive streaming, danmaku overlay, subtitles, multi-part videos.
- **Audio-only mode** — any video can play audio only. Good for music, podcasts, or saving bandwidth.
- **Live** — stable forwarding proxy, heartbeat keep-alive, real-time chat.
- **Bangumi** — browse and select episodes, with optional Nyaa.si torrent search.
- **Articles & Opus** — Bilibili `cv` articles and `opus` posts rendered as clean proxy pages.
- **Global search** — videos, uploaders, articles, live rooms, bangumi, with filters.
- **Privacy by default** — all media goes through a server-side proxy (client IPs never reach Bilibili's CDN). No account, zero tracking.

## Guide

| Section | Audience |
| :--- | :--- |
| [Operator guide](operators/) | People deploying and running an instance (Docker, Caddy, Redis, credentials). |
| [Developer guide](developers/) | Contributors working on the Quart app, player, themes, or the Bilibili API wrapper. |

## Quick links

- **Use it** — open your instance URL (default local Docker address: `http://localhost:8000`).
- **Deploy it** — see [Quickstart with Docker](operators/quickstart-docker); two commands and you're running.
- **Understand it** — see [Architecture](developers/architecture): Caddy, Granian/Quart, Redis, and the media proxy.
- **Source** — [apicalshark/mikuinvidious](https://github.com/apicalshark/mikuinvidious), GNU GPL-3.0.
