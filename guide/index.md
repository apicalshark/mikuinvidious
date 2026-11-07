# User Guide Overview

Welcome to MikuInvidious — a free, lightweight, privacy-focused web frontend for Bilibili, inspired by Invidious.

MikuInvidious allows you to search, watch, read, and listen to Bilibili content without official apps, intrusive trackers, analytics scripts, or account requirements.

---

## Why MikuInvidious?

- **Zero Tracking & No Account Needed**: Browse videos, anime, articles, and user spaces without logging in. Your viewing habits, IP address, and browser fingerprints are never transmitted to Bilibili.
- **Privacy-First Proxy Architecture**: All media streams (video, audio, live streams) and images are fetched by the MikuInvidious server and forwarded to your browser. Bilibili sees only the instance server (or its egress proxy).
- **Universal Search Bar**: Paste any Bilibili link — including `b23.tv` shortlinks, full web URLs, or direct IDs — directly into the search bar to immediately open the content with all tracking parameters stripped.
- **Listen Mode (`?listen=1`)**: An audio-only interface for any video, saving CPU and bandwidth on mobile devices and background tabs.
- **Resilient Media Playback**: High-definition DASH video streaming with automatic CDN mirror failover, danmaku (bullet comments) overlay, and background multi-track MP4 downloading.
- **Clean UI & Themes**: Responsive modern interface powered by Tailwind CSS with system, dark, and light modes, plus multi-language localization.

---

## Interface Tour

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:c4ad2de0c162287fb30ae742ced998dd7bddbd931ece2a59b3bb41a6c5448235 -->

1. **Top Navigation Bar**:
   - **Search Bar**: Universal input supporting keywords, IDs, and official Bilibili URLs.
   - **Anime / Bangumi Index**: Browse seasonal anime, movies, and documentary series.
   - **Theme Selector**: Quick toggle between Light, Dark, and System color schemes.
   - **Language Menu**: Switch between English, 正體中文 (Traditional Chinese), 简体中文 (Simplified Chinese), and 日本語 (Japanese).
   - **History**: Opened video IDs are stored server-side in Redis and associated with a browser cookie. The History page reads those IDs from Redis.
   - **Preferences**: Customize default video playback quality, danmaku display options, and player behavior.

<!-- /coderabbit-ai-generated:v1 -->

2. **Content Views**:
   - **Home / Feeds**: Popular trending videos and sub-zone discovery.
   - **Video Player**: High-definition player with multi-part episode selectors, tags, related recommendations, and comment threads.
   - **Bangumi Pages**: Series synopsis, episode grid, and torrent search integration.
   - **User Space**: Creator profiles, uploaded video lists, dynamic articles, and JSON feeds.
   - **Live Rooms**: Real-time live stream player with chat and format selectors (FLV / HLS).

---

## Next Steps

- [Bilibili links & search bar](search-and-links): Learn how the universal search bar resolves any link and cleans tracking parameters.
- [URL routes & features](url-syntax): Discover direct URL navigation shortcuts and power-user parameters.
- [Player & downloads](playback-and-downloads): Master player hotkeys, danmaku controls, quality tiers, and background downloads.
