# Live Stream Keep-Alive Strategy

To prevent browser disconnections and proxy timeouts during long-running FLV streams, we employ a multi-layered keep-alive strategy.

## 1. Timeout Configuration

Standard HTTP servers often have a 60-second default timeout for responses or idle connections. We have extended these at every level:

| Layer | Configuration Variable | Value |
| :--- | :--- | :--- |
| **Nginx** | `proxy_read_timeout` / `keepalive_timeout` | 10800s |
| **Hypercorn** | `response_timeout` / `keep_alive_timeout` | None / 10800s |
| **Quart** | `RESPONSE_TIMEOUT` / `BODY_TIMEOUT` | 10800s |
| **HTTPX** | `httpx.Timeout` (Global) | None (Infinite) |

## 2. FLV Heartbeats (In-Stream)

If the upstream Bilibili server stops sending data (e.g., during handshakes or silence), the proxy generator sends a minimal valid FLV tag to keep the TCP connection active without corrupting the stream.

- **Tag Type**: 18 (Script Data)
- **Tag Size**: 0
- **Payload**: `\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x0b` (Includes PrevTagSize)
- **Interval**: Every 15 seconds of inactivity.

## 3. Client-Side Buffering

The `mpegts.js` player is configured with a `stashInitialSize` of 16KB. This provides enough head-room for the player to parse the initial FLV header and metadata before timing out.

## 4. Reconnection Logic

The `LiveStreamManager` in `player.js` monitors the playback health:
- **Stall Detection**: Reconnects if playback speed remains 0 for >15s.
- **Latency Chase**: Increases playback rate (1.05x) if latency exceeds 1.5s.
- **Hard Jump**: Skips forward if latency exceeds 5s.