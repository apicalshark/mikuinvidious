# Player & Downloads

MikuInvidious features an advanced media playback and background download engine designed for resilience, zero client-side tracking, and smooth performance.

---

## 1. Video Player & Quality Selection

The video player uses `dash.js` (v5 representations API) with a modern, mobile-friendly interface.

### Quality Tiers
- **Anonymous Tier (Default)**: Supported up to **1080P High Definition** (1080P, 720P, 480P, 360P). No login or account required.
- **Premium / 4K Tier**: Qualities such as **1080P 60fps**, **4K Ultra HD**, and paid episodes are accessible if the instance administrator has configured server-side Bilibili credentials.

### Keyboard Shortcuts

When the video player is in focus, you can use standard keyboard shortcuts:

| Key | Action |
| :--- | :--- |
| <kbd>Space</kbd> or <kbd>K</kbd> | Play / Pause |
| <kbd>→</kbd> / <kbd>←</kbd> | Seek forward / backward 5 seconds |
| <kbd>L</kbd> / <kbd>J</kbd> | Seek forward / backward 10 seconds |
| <kbd>↑</kbd> / <kbd>↓</kbd> | Volume up / down (5%) |
| <kbd>M</kbd> | Toggle Mute |
| <kbd>F</kbd> | Toggle Fullscreen |
| <kbd>D</kbd> | Toggle Danmaku (bullet comments) visibility |

---

## 2. Danmaku (Bullet Comments)

Danmaku comments are loaded as server-converted XML and rendered smoothly over the video canvas.

- **Toggle**: Use the danmaku icon in the player control bar or press <kbd>D</kbd>.
- **Customization**: Under the settings cog in the player, you can tune:
  - **Opacity**: Adjust transparency from solid to faint so danmaku doesn't obscure subtitles.
  - **Font Size**: Scale comment size to fit desktop or mobile displays.
  - **Display Speed**: Control how fast comments float across the screen.

---

## 3. Playback Resilience & Mirror Failover

MikuInvidious's media proxy handles all upstream Bilibili CDN connections server-side:
- **Automatic Mirror Failover**: If a CDN edge slows down or drops the connection, the server automatically fails over to alternative healthy mirrors within seconds, maintaining seamless playback.
- **Socket Reuse**: Upstream TCP+TLS connections are pooled across video fragments, eliminating repeated handshake overhead and reducing playback buffering.
- **Privacy Assurance**: Your browser never connects directly to Bilibili CDN IP addresses (`*.bilivideo.com`).

---

## 4. Background Download Manager

Any video can be downloaded directly from the video page when enabled by the instance operator.

```
+-----------------------------------------------------------+
| [Download 1080P]                                          |
|                                                           |
| 1. Queue Job  --> 2. Segment Download --> 3. ffmpeg Mux   |
| (Multi-track)     (Adaptive Pacing)       (Faststart MP4) |
+-----------------------------------------------------------+
```

### How to Download
1. On any video page, click the **Download** button located below the player.
2. Select your desired resolution.
3. A floating download drawer appears at the bottom of the screen.

### Download Phases
The download status updates in real-time through the following phases:
1. **Queued**: The job is registered in the server's background download queue.
2. **Resolving**: The server fetches fresh high-resolution video and audio CDN stream signatures.
3. **Downloading**: Video and audio tracks are downloaded concurrently in 32 MB segments with an adaptive watchdog that avoids CDN rate throttling.
4. **Muxing**: The server muxes the isolated video and audio streams using `ffmpeg -c copy -movflags +faststart` into a clean, seekable `.mp4` file.
5. **Ready**: The file is ready and automatically prompts your browser to save it to your local storage.

> [!TIP]
> You can navigate to other pages, watch other videos, or search while a download is running. The background job will continue processing on the server. If you need to stop, click **Cancel** to immediately abort upstream connections and delete temporary files.

---

## 5. Live Stream Playback

Live stream rooms support real-time audio/video streaming via raw sockets:
- **Format Policy**: By default, live rooms stream using high-speed FLV (via `mpegts.js`), automatically falling back to HLS master playlists (via `hls.js`) if required.
- **Real-Time Danmaku**: Connects to Bilibili live room chat websockets server-side and broadcasts comments to your browser with low latency.
