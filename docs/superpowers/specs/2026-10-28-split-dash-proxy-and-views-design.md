# Design Specification: Split `dash_proxy.py` and `views.py` into Flat Modules

- **Date:** 2026-10-28
- **Topic:** Modularize `dash_proxy.py` and `views.py` to enforce file length limit (< 1000 lines) using flat sibling modules (Option B).
- **Target Files:**
  - `python/dash_proxy.py` (3,884 lines -> facade < 150 lines)
  - `python/views.py` (1,844 lines -> facade < 120 lines)

---

## 1. Intent and The Why

`python/dash_proxy.py` has grown to 3,884 lines, combining playurl fetching, caching, MPD generation, DASH track proxying, mirror health tracking, and background download pipelines. Similarly, `python/views.py` has reached 1,844 lines, housing home/search, UP space, live streaming, video detail, and audio routes.

These monolithic files exceed the project maintainability standard and the user-specified ceiling of 1,000 lines per file. They increase cognitive load and merge conflicts. Splitting them into focused, cohesive modules under 1,000 lines while retaining 100% backward compatibility for all imports and tests ensures long-term maintainability.

---

## 2. Goals, Non-Goals, and Anti-Goals

### Goals
1. **Strict Line Length Ceiling:** Every Python file in the codebase must have fewer than 1,000 lines (target: 100 to 850 lines).
2. **Zero Behavior Change:** API endpoints, HTTP routes, cache semantics, streaming headers, download jobs, error handling, and timeout behavior must remain identical.
3. **100% Backward Compatibility:** Any existing import (`import dash_proxy`, `from dash_proxy import ...`, `import views`, `from views import ...`) and unittest mock/patch (`patch("views.resolve_b23_target")`, `patch.object(dash_proxy, "_yield_dash_body")`, etc.) MUST continue to work without modification.
4. **All Tests and Linters Pass:** `npm run test` (all 78 unit tests) and `npm run lint:python` (`ruff check python`) must pass cleanly.

### Non-Goals
1. Refactoring business logic, modifying algorithms, or rewriting caching/proxying routines.
2. Converting routes to nested Blueprint url prefixes or changing endpoint names.
3. Modifying frontend scripts or templates.

### Anti-Goals
1. Circular import cycles between newly split modules.
2. Breaking runtime monkeypatching or testing mocks that expect symbols to exist in `dash_proxy` or `views`.
3. Creating deeply nested subdirectories when the user explicitly selected Option B (flat sibling modules in `python/`).

---

## 3. Constraints

- **Python Version:** >= 3.15 (uv environment).
- **Linter & Formatter:** `ruff check python` with project configuration (line length 120, pyflakes, isort, etc.).
- **Quart App & Blueprint Registration:**
  - `dash_proxy_bp` must remain registered in `app.py`.
  - Global view routes must remain attached to `app` (as `@app.route(...)`).
- **Shared State & Memory:**
  - `dash_proxy._download_jobs` must remain accessible and shared with background tasks and `app.py`.
  - `dash_proxy._mirror_health` and `dash_proxy._mirror_range_shy` dictionaries must remain singletons across track streaming and downloads.

---

## 4. Architecture & Module Decomposition (Option B: Flat Sibling Modules)

### 4.1 DASH Proxy Modules (`python/`)

`dash_proxy.py` (originally 3,884 lines) is divided into six flat sibling modules plus one facade:

1. **`python/dash_common.py` (~450 lines):**
   - **Responsibility:** Common constants, URL validation/sanitization, range-parsing helpers, mirror health tracking, and CDN request headers.
   - **Exports:**
     - Constants: timeout constants (`DASH_PLAYBACK_ATTEMPT_TIMEOUT`, `DASH_BODY_IDLE_TIMEOUT`, `DASH_BODY_GRACE`, `DASH_BODY_FLOOR_KBPS`, etc.), size limits (`_FREE_DOWNLOAD_MAX_QN`, `_DEFAULT_MAX_DOWNLOAD_MB`, `_DOWNLOAD_TOO_LARGE`, `_DOWNLOAD_CANCELLED`, `_TRACK_DOWNLOAD_RETRY_DELAYS`).
     - Mirror State & Helpers: `_mirror_health`, `_mirror_range_shy`, `_note_mirror_ok`, `_note_mirror_bad`, `_mirror_last_fail`, `_mirror_is_sick`, `_order_urls_by_health`, `_note_mirror_range_shy`, `_mirror_range_shy_at`, `_segment_mirror_order`.
     - URL & Validation: `_is_mcdn_url`, `_pick_stable_dash_url`, `_is_safe_dash_url`, `_is_safe_dash_url_async`, `_is_valid_vid`, `_safe_vid`, `_safe_download_filename`, `_extract_ep_id`, `_build_dash_cdn_headers`, `_pace_delay`.
     - Range Helpers: `_parse_content_range_full`, `_parse_416_total`, `_parse_range_start`, `_parse_content_range_start`, `_shift_dash_range`, `_expected_resume_start`.
     - Exceptions: `_TrackCut`, `_CUT_ERRORS`, `_RETRYABLE_TRACK_ERRORS`.

2. **`python/dash_fetch.py` (~700 lines):**
   - **Responsibility:** DASH & progressive durl playurl fetching, Redis caching, expiry detection, and quality selection.
   - **Exports:**
     - Track inspection: `_lookup_track`.
     - Cache & expiry: `_load_dash_data`, `_refresh_dash_data`, `_fetch_and_cache_dash_data`, `_playurl_url_expiry`, `_dash_data_urls_stale`.
     - Playurl fetch & parsing: `video_get_dash_for_qn`, `_video_get_dash_for_qn_uncached`, `_dash_fetch_join`, `_dash_fetch_settle`, `_normalize_track_urls`, `_parse_pgc_playurl`, `_paywall_short_circuit`, `has_valid_dash_tracks`.
     - durl fallback: `fetch_durl_supported_src`, `_fetch_single_durl`, `_fetch_single_durl_pgc`, `_durl_quality_list`, `_cache_durl_entry`, `_select_durl_results`, `_choose_durl_entry`, `_DurlResolveError`, `_resolve_durl_download`.

3. **`python/dash_mpd.py` (~250 lines):**
   - **Responsibility:** MPD (Media Presentation Description) XML generation for dash.js and manifest HTTP endpoint.
   - **Exports:**
     - Helpers: `_sb_initialization_range`.
     - MPD XML generator: `generate_vod_mpd`.
     - Route handler: `video_dash_manifest_view` (attached directly to `app` as `@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")` to preserve endpoint name `video_dash_manifest_view`).

4. **`python/dash_stream.py` (~800 lines):**
   - **Responsibility:** DASH track fragment proxying, connection failover, idle watchdog, and live fragment responses.
   - **Exports:**
     - Stream exceptions: `_DashUpstreamError`, `_SlowDashBody`.
     - Resumed connection wrapper: `_ResumedDashConn`.
     - Stream helpers: `_dash_candidate_urls`, `_fetch_dash_attempt`, `_dash_handshake`, `_refresh_dash_ticket`, `_dash_response_lengths`, `_yield_dash_body`, `_failover_dash_conn`, `_fetch_dash_track`, `_retry_proxy_with_fresh_playurl`, `_dash_ip_key`, `_dash_frag_key`.
     - Route handler: `proxy_dash` (route `/proxy/dash/<vid>/<int:idx>/<media_type>/<int:qn>/<int:cid>` on `dash_proxy_bp`).

5. **`python/dash_download_track.py` (~750 lines):**
   - **Responsibility:** Segmented track downloading, body streaming, connection retry handling, and ffmpeg audio/video muxing.
   - **Exports:**
     - Track download helpers: `_max_download_size_mb`, `_max_download_track_bytes`, `_download_size_status`, `_pick_download_tracks`, `_open_cdn_track`, `_await_track_retry`, `_download_track_to_file`, `_validate_track_response`, `_fetch_track_attempt`, `_stream_track_body`.
     - Segmented engine: `_has_exact_content_length`, `_download_track_segmented`, `_download_track_file`, `_peek_content_length`.
     - Muxing: `_mux_tracks`.

6. **`python/dash_download_job.py` (~800 lines):**
   - **Responsibility:** Background download jobs, job registry & lifecycle, concurrency limiter, status/file/cancel routes, and legacy streaming download.
   - **Exports:**
     - Classes: `DownloadCapacityError`, `_JobCancelled`, `_DownloadJob`.
     - Job state: `_download_jobs`, `_download_jobs_lock`, `_download_limiter`.
     - Job management: `create_download_job`, `cancel_download_job`, `_get_job`, `_sweep_jobs`, `_remove_job_tmpdir`, `_run_download_job`, `_run_dash_job`, `_run_durl_job`.
     - Route handlers on `dash_proxy_bp`: `download_status`, `download_file`, `download_cancel`, legacy `proxy_download`, `_proxy_download_durl_fallback`.

7. **`python/dash_proxy.py` (Facade, ~120 lines):**
   - Defines `dash_proxy_bp = Blueprint("dash_proxy", __name__)`.
   - Registers Blueprint routes (`proxy_dash`, `download_status`, `download_file`, `download_cancel`, `proxy_download`).
   - Re-exports all public and internal symbols from `dash_common`, `dash_fetch`, `dash_mpd`, `dash_stream`, `dash_download_track`, and `dash_download_job` to preserve 100% backward compatibility for imports and test mocks.

---

### 4.2 Views Modules (`python/`)

`views.py` is divided into six cohesive modules:

1. **`python/views_home.py` (~350 lines):**
   - **Routes & Handlers:**
     - `/` (`home_view`)
     - `/zone/<zid>` (`zone_id_view`)
     - `/search` (`search_view`)
     - `/history` (`history_view`)
     - `/convert` (`convert_view`)
     - `/api/convert` (`api_convert`)
     - `/licenses` (`static_licenses_view`)
     - B23 helper: `resolve_b23_target`

2. **`python/views_space.py` (~450 lines):**
   - **Routes & Handlers:**
     - `/space/<mid>` (`space_view`)
     - `/space/<mid>/json` (`space_json_feed`)
     - `/author/<mid>` (`author_view`)
     - `/read/<cid>` (`read_view`)
   - **Cache Helpers:**
     - `_space_data_key`, `_is_good_space_data`, `_max_space_ttl`, `_read_space_data`, `_read_space_data_stale`, `_write_space_data`, `_fetch_space_data`, `_space_html_response`.

3. **`python/views_live.py` (~320 lines):**
   - **Routes & Handlers:**
     - `/live` (`live_list_view`)
     - `/live/<room_id>` (`live_room_view`)
     - `/live/chat/sse/<room_id>` (`live_chat_sse`)
   - **Helpers:**
     - `_pick_live_url`, `_live_fetchable_qualities`.

4. **`python/views_video.py` (~550 lines):**
   - **Routes & Handlers:**
     - `/video/<vid>` and `/video/<vid>/<int:idx>` (`video_view`)
     - `/video/<vid>/listen` and `/video/<vid>/<int:idx>/listen` (`video_listen_view`)
     - `/api/component/player/<vid>/<int:idx>` (`api_component_player`)
     - `/api/component/meta/<vid>/<int:idx>` (`api_component_meta`)
     - `/api/component/comments/<vid>/<int:idx>/more` (`api_component_meta_more`)
     - `/api/component/sub_comments/<vid>/<rpid>` (`api_component_sub_comments`)
   - **Helpers:**
     - `_is_final_play_data`, `_resolve_progressive_sources`, `_get_subtitles`.

5. **`python/views_audio.py` (~200 lines):**
   - **Routes & Handlers:**
     - `/audio/<auid>` (`audio_view`)
     - `/audio/menu/<amid>` and `/audio/menu/<amid>/<int:idx>` (`audio_list_view`)

6. **`python/views.py` (Facade, ~100 lines):**
   - Imports and registers routes on `app` from `views_home`, `views_space`, `views_live`, `views_video`, and `views_audio`.
   - Re-exports all route functions and internal helpers (e.g. `resolve_b23_target`, `space_view`, `video_view`, etc.) so that `app.py`, `tests/`, and templates referencing `views.*` or endpoints continue to function without change.

---

## 5. Verification Plan

1. **Static Analysis & Linting:**
   - Run `uv run ruff check python` -> must pass with 0 errors.
   - Run line-count check `wc -l python/*.py` -> verify no file exceeds 1,000 lines.
2. **Compilation Check:**
   - Run `uv run python -m compileall -f python tests tools` -> ensure no syntax or import errors.
3. **Automated Unit Tests:**
   - Run `npm run test` (`PYTHONPATH=python uv run python -m unittest discover tests`) -> all 78 tests must pass.
4. **Specific Regression Validations:**
   - Verify `tests/test_b23_routes.py` (checks `views.resolve_b23_target` patches).
   - Verify `tests/test_convert_view.py` (checks `views.resolve_b23_target` and `views.homepage.get_videos`).
   - Verify `tests/test_review_regressions.py` (checks `views.cache_minutes`, `views.space_view`, `views.video_view`, `dash_proxy._yield_dash_body`, `dash_proxy._ResumedDashConn`, `dash_proxy._failover_dash_conn`, etc.).
