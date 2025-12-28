# Phase 4 Stability & Performance Fixes

This document details the architectural changes made to stabilize Bilibili live streaming and Danmaku SSE connections.

## The 60-Second Timeout Problem

Users reported that live streams and danmaku would consistently cut off after exactly 60 seconds. This was identified as a "chain of bottlenecks" where multiple layers of the stack defaulted to a 60-second idle or response timeout.

### Timeout Bottlenecks
| Layer | Default Timeout | Resolution |
| :--- | :--- | :--- |
| **Hypercorn** | 60s (`response_timeout`) | Set to `None` in `main.py` and environment. |
| **Quart** | 60s (`RESPONSE_TIMEOUT`) | Set to 10800s (3 hours) in `shared.py`. |
| **Nginx** | 60s (`proxy_read_timeout`) | Set to 10800s in `nginx.conf`. |
| **HTTPX** | 5s - 60s (Global) | Set to `None` for the async client in `shared.py`. |

## Multi-Layered Keep-Alive Strategy

To ensure connections remain active for up to 3 hours (10800 seconds) without being reaped by intermediate proxies or ISPs, we implemented the following:

### 1. ASGI Server (Hypercorn)
- `keep_alive_timeout` set to 10800s.
- `response_timeout` set to `None` to allow infinite streaming tasks.

### 2. Reverse Proxy (Nginx)
- `proxy_buffering off;`: Critical for real-time FLV delivery.
- `keepalive_timeout 10800;`: Keeps the connection between browser and Nginx alive.
- `proxy_read_timeout 10800s;`: Prevents Nginx from killing the backend connection during periods of low activity.

### 3. Application Proxy (Quart)
- **FLV Heartbeats**: The `proxy.py` generator sends a Type 18 (Script Data) FLV tag every 15 seconds if no data is received from Bilibili. This keeps the TCP socket "hot."
- **Keep-Alive Headers**: Explicitly sends `Keep-Alive: timeout=10800` to the client.

## Client-Side Optimizations

The `mpegts.js` configuration in `static/themes/modern/js/player.js` was updated:
- `stashInitialSize: 16384`: Increased to 16KB to prevent the player from stalling while waiting for the initial stream metadata.
- **Reconnection Logic**: Implemented exponential backoff and stall detection to gracefully handle transient network jitter.

## Resource Safety
While 3 hours is a long duration, it acts as a safety net. If a client disconnects ungracefully, the server will eventually reap the "ghost" connection after 3 hours, preventing a permanent memory leak.

## Video Aspect Ratio & Centering Fix

Non-16:9 videos (e.g., 21:9 ultrawide movie trailers) were previously sticking to the top of the container during fullscreen playback. This created a misalignment between the video content and the Danmaku layer, which is dynamically centered.

### Resolution
The `#player` element (the `<video>` tag) was updated with the following CSS strategy:
- **`object-fit: contain`**: Ensures the video scales to fit while maintaining its original aspect ratio.
- **`object-position: center center`**: Explicitly centers the video pixels within the element's box.
- **Absolute Positioning**: Forced the element to fill the `100% x 100%` area of the `media-controller` to prevent browser or library-specific layout rules from pulling it to the top.

This ensures that black bars are distributed equally at the top and bottom (or sides), and that danmaku overlays perfectly on top of the actual video pixels.

## Thumbnail & Avatar Loading Optimizations

Previously, live stream search results and some other pages were experiencing slow thumbnail loading due to several bottlenecks:
1. **Low Concurrency**: The `image_limiter` was set to a strict `10` concurrent requests, causing "head-of-line blocking" on pages with many images.
2. **Missing Resizing**: Some images (especially author faces in live search results) were loading full-resolution original files.
3. **Redundant Loading**: Live search results were fetching both covers and faces, doubling the load compared to regular category views.

### Resolution
- **Increased Concurrency**: The `image_limiter` in `python/shared.py` was increased from `10` to `50`.
- **Enforced Resizing**: All avatars and thumbnails now use Bilibili CDN resize suffixes (e.g., `@48w_48h.webp`, `@320w_180h.webp`) to reduce payload and proxy processing time.
- **Robust Filter**: The `|pic` Jinja2 filter was improved to handle various URL formats (missing protocols, full URLs, etc.) more safely.
- **Optimized Templates**: `search.html`, `home.html`, `space.html`, `author.html`, and `video.html` were all updated to request appropriately sized assets.

## Autoplay Blocking & User Feedback

Modern browsers strictly block autoplay with sound. Previously, this would cause videos to appear "stuck" or fail silently without explaining to the user that an interaction was required.

### Resolution
- **In-Player Overlay**: Instead of a global site-wide prompt, a dedicated "Click to Play" overlay was added directly onto the video player (`templates/macros.html`).
- **Failure Detection**: The player's `play()` promise is now monitored in `player.js`. If the browser rejects the autoplay attempt with a `NotAllowedError`, the overlay is triggered.
- **Visual Feedback**: The overlay uses a Material-style backdrop blur and a clear play icon to guide the user to interact with the player, satisfying the browser's requirement for a user-initiated gesture.
