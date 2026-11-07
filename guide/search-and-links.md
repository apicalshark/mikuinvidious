# Bilibili Links & Search Bar

The search bar in MikuInvidious is an intelligent dispatcher. In addition to performing standard keyword searches, it automatically parses official Bilibili URLs, strips telemetry tracking parameters, and routes raw identifiers directly to their destination.

---

## 1. Pasting Bilibili Share Links & Shortlinks

When you click "Share" in official Bilibili web pages or mobile apps, the link generated is typically a `b23.tv` shortlink:

```text
https://b23.tv/AbCdEf
```

> [!WARNING]
> Official `b23.tv` links contain embedded tracking identifiers (such as `share_source`, `share_medium`, `bbid`, and `ts`) that Bilibili uses to link the sharer's account with the recipient's browsing fingerprint.

When you paste any `b23.tv` link into the MikuInvidious search bar:
1. **Server-Side Resolution**: The MikuInvidious backend performs an upstream HTTP redirect lookup without passing your browser's identity.
2. **Telemetry Sanitization**: All surveillance query parameters (`spm_id_from`, `from_source`, `share_*`, `mid`, etc.) are stripped.
3. **Instant Redirection**: You are immediately redirected to the clean video or resource page (e.g., `/video/BV1xx411c7mD`).

---

## 2. Supported URL Formats

You can paste any of the following official Bilibili URLs directly into the search bar:

| URL Type | Example Bilibili Link | MikuInvidious Destination |
| :--- | :--- | :--- |
| **Video (BV)** | `https://www.bilibili.com/video/BV1xx411c7mD` | `/video/BV1xx411c7mD` |
| **Video (av)** | `https://www.bilibili.com/video/av170001` | `/video/av170001` |
| **Video with Part** | `https://www.bilibili.com/video/BV1xx411c7mD?p=3` | `/video/BV1xx411c7mD?p=3` |
| **Anime Episode** | `https://www.bilibili.com/bangumi/play/ep312456` | `/bangumi/play/ep312456` |
| **Anime Season** | `https://www.bilibili.com/bangumi/play/ss28241` | `/bangumi/view/28241` |
| **User Space** | `https://space.bilibili.com/1234567` | `/space/1234567` |
| **Live Room** | `https://live.bilibili.com/545068` | `/live/545068` |
| **Article (cv)** | `https://www.bilibili.com/read/cv1234567` | `/read/1234567` |
| **Opus Post** | `https://www.bilibili.com/opus/1234567890` | `/opus/1234567890` |
| **Audio Track** | `https://www.bilibili.com/audio/au123456` | `/audio/123456` |
| **Audio Playlist** | `https://www.bilibili.com/audio/am123456` | `/audio_list/123456` |

---

## 3. Direct ID Lookups

You do not need to paste the full URL. If you have an ID from a video, post, or channel, simply paste the ID into the search bar:

- `BV1xx411c7mD` or `bv1xx411c7mD` → Directly opens the video.
- `av170001` or `AV170001` → Directly opens the legacy AV video.
- `ep312456` → Directly opens the Bangumi episode.
- `ss28241` → Directly opens the Bangumi season overview.
- `cv1234567` → Directly opens the article.
- `au123456` → Directly opens the audio track.

---

## 4. Keyword Searching & Filters

If your search input is not a recognized URL or ID pattern, MikuInvidious treats it as a search query.

### Search Categories
You can filter results by category tab:
- **Videos**: General UGC video uploads.
- **Bangumi / Anime**: Official anime, movies, and TV series.
- **Users**: Channel creators and uploaders.
- **Live**: Active live broadcasting rooms.

### Video Search Filters
When searching for videos, the filter dropdown lets you refine:
- **Sort Order**:
  - *Comprehensive / Total Rank* (default relevance)
  - *Most Viewed* (click count)
  - *Latest Published* (pubdate)
  - *Most Danmaku*
  - *Most Favorites / Bookmarks*
- **Duration**:
  - *All Durations*
  - *Under 10 minutes*
  - *10 to 30 minutes*
  - *30 to 60 minutes*
  - *Over 60 minutes*
- **Partitions**: Narrow search results to specific genres (Music, Animation, Gaming, Technology, Knowledge, etc.).
