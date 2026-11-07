# URL Routes & Features

MikuInvidious is designed with clean, predictable URL patterns. You can bookmark, share, or directly navigate to any route.

---

## 1. Video Routes

### Standard Video Player: `/video/<bvid>`

- **Example**: `/video/BV1xx411c7mD` or `/video/av170001`
- Loads video metadata, dynamic ISO/IEC 23009-1 DASH streaming manifest, danmaku comments, related recommendations, and comment threads.

### Multi-Part Videos: `/video/<bvid>:<index>`

- **Example**: `/video/BV1xx411c7mD:1`
- Specifies which episode or part of a multi-part upload to play. Part index starts at `0`, so this example opens the second part.

### Listen Mode: `?listen=1`

- **Example**: `/video/BV1xx411c7mD?listen=1`
- Switches the video view into **Listen Mode**:
  - Drops the heavy video canvas and decoder pipeline.
  - Loads an audio-only player streaming the highest available DASH audio track.
  - Significantly reduces cellular data consumption, battery drain, and thermal throttling in background browser tabs.
  - Retains video metadata, description, and comments.

---

## 2. Anime & Bangumi Routes

### Season Overview: `/bangumi/view/<ssid>`

- **Example**: `/bangumi/view/28241`
- Displays the complete series synopsis, season metadata, actor/character index, PV clips, and a grid of all available episodes.
- **Nyaa.si Torrent Integration**: When enabled by the instance operator (`nyaa_bangumi = true`), collection resources appear in the sidebar and matching episode resources appear in episode rows.

### Episode Player: `/bangumi/play/<epid>`

- **Example**: `/bangumi/play/ep312456`
- Opens the specific episode player with the complete episode rail on the right side.

---

## 3. Creator Space & Feeds

### User Profile & Uploads: `/space/<mid>`

- **Example**: `/space/1234567`
- Displays creator statistics, banner, avatar, biography, and chronological uploaded videos with pagination (`?i=2`).

### JSON Feed: `/space/<mid>/json`

- **Example**: `/space/1234567/json`
- A clean, unauthenticated machine-readable JSON feed of the author's latest uploaded videos. Suitable for automation, external RSS bridges, or personal scrapers.

---

## 4. Live Streaming: `/live/<room_id>`

- **Example**: `/live/545068`
- Opens the live stream player.
- Streams live audio and video via raw-socket CDN proxying with automatic keep-alive heartbeats.
- Displays live room title, streamer info, and real-time danmaku chat.

---

## 5. Articles & Dynamic Posts

### Clean Article Reader: `/read/<cvid>`

- **Example**: `/read/1234567`
- Formats Bilibili column articles (`cv` articles) into a clean, distraction-free reading layout without upstream scripts or tracking beacons. All inline images are proxied through the server.

### Dynamic Opus Reader: `/opus/<id>`

- **Example**: `/opus/1234567890`
- Displays mobile-first Opus dynamic posts with attached multi-image galleries.

---

## 6. Music & Audio

### Audio Track Player: `/audio/<auid>`

- **Example**: `/audio/123456`
- Audio playback interface featuring lyrics display, cover art, and author credits.

### Audio Playlist: `/audio_list/<amid>`

- **Example**: `/audio_list/123456`
- Continuous playlist player for full music albums or user-curated song collections.

---

## 7. Utility & User Pages

### Shortlink Converter: `/convert?url=<url>`

- **Example**: `/convert?url=https://b23.tv/AbCdEf`

- The `url` parameter prefills the converter form. Select **Convert**, then open the resulting MikuInvidious link to visit the sanitized destination.

### Watch History: `/history`

- Displays opened video IDs read from server-side Redis storage.
- **Privacy Note**: A browser cookie associates your browser with those IDs in Redis.

### Preferences: `/preferences`

- Configure local settings:
  - Theme mode (Auto, Light, Dark).
  - Language selection.
  - Preferred video streaming quality default.
  - Danmaku speed, opacity, font size, and display toggles.
