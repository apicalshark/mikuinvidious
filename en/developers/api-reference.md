# Route & API reference

## Pages (`views.py`, `views_bangumi.py`, `app.py`)

| Route | Description |
| :--- | :--- |
| `/` | home feed (WBI `top/feed/rcmd`) |
| `/search` | search (video / uploader / article / live / bangumi tabs) |
| `/video/<vid>`, `/video/<vid>:<idx>` | video page, parts |
| `/video_listen/<vid>[:<idx>]` | audio-only |
| `/video/dash/<vid>/<idx>/manifest.mpd` | DASH manifest (`?fresh=1` forces refresh) |
| `/live`, `/live/<room_id>` | live directory, rooms |
| `/live/chat/<room_id>` | chatroom SSE |
| `/space/<mid>`, `/space/<mid>/json` | uploader space, JSON feed |
| `/author/<mid>` | author page |
| `/read/<cid>`, `/read/mobile/<cid>`, `/opus/<cid>` | articles / posts |
| `/audio/<auid>`, `/audio_list/<amid>[:<idx>]` | tracks / playlists |
| `/bangumi`, `/bangumi/view/<ssid>`, `/bangumi/play/ep<id>` | bangumi index / series / episode |
| `/bangumi/api/nyaa/<ssid>` | Nyaa search API |
| `/history`, `/preferences`, `/licenses`, `/robots.txt` | history / preferences / JS licenses / crawler policy |
| `/<b32tvid>` | short-ID entry |
| `/vv/<zid>` | zone feed |

## Media / resource proxy

| Route | Description |
| :--- | :--- |
| `/proxy/<subpath>` | generic proxy (images to WebP, progressive media) |
| `/proxy/dash/<vid>/<idx>/<type>/<qn>/<cid>` | DASH track proxy (Range passthrough) |
| `/proxy/download/<vid>/<idx>/<qual>` | legacy download, non-JS environments |
| `/proxy/live/disconnect` (POST) | tear down live forwarding |
| `/res/danmaku/<vid>[:<idx>]` | danmaku XML |
| `/res/subtitle/<vid>[:<idx>[:<lan>]]` | subtitles |

## Download jobs / component API

| Route | Description |
| :--- | :--- |
| `/download` (POST) | create job → `{"job_id"}` |
| `/download/status/<job>` | poll progress |
| `/download/file/<job>` | fetch finished file |
| `/download/cancel/<job>` (POST) | cancel |
| `/api/component/player/<vid>/<idx>` | player component (`is_dash` / `dash_url`) |
| `/api/component/meta/<vid>/<idx>` | info component |
| `/api/component/comments/<vid>/<idx>/more`, `/api/component/comments/<vid>/<rpid>` | more comments / threads |
| `/toggle_theme`, `/set_lang` (POST) | theme / language cookies |

Live format policy (FLV first, `LIVE_PREFER_HLS` reverses; never switched
mid-stream) is server-side. Templates pass the loader choice via
`window.live_format` (mpegts vs hls). VOD pages load neither.
