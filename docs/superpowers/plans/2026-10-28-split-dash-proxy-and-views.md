# Split `dash_proxy` and `views` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modularize `python/dash_proxy.py` (3,884 lines) and `python/views.py` (1,844 lines) into flat sibling modules in `python/` such that every single Python file has fewer than 1,000 lines while preserving 100% backward compatibility and test mock integrity.

**Architecture:** Split `python/views.py` into five domain modules (`views_home.py`, `views_space.py`, `views_live.py`, `views_video.py`, `views_audio.py`) and a facade `views.py`. Split `python/dash_proxy.py` into six domain modules (`dash_common.py`, `dash_fetch.py`, `dash_mpd.py`, `dash_stream.py`, `dash_download_track.py`, `dash_download_job.py`) and a facade `dash_proxy.py`. Both facades dynamically synchronize attribute access and mutations across submodules so that existing `unittest.mock.patch` calls continue to work seamlessly.

**Tech Stack:** Python 3.15+, Quart, Granian, Redis, httpx, curl_cffi, ruff.

**Spec:** `docs/superpowers/specs/2026-10-28-split-dash-proxy-and-views-design.md`

## Global Constraints

- **File Length Limit:** Every Python file in `python/` MUST strictly contain fewer than 1,000 lines (target: 100 to 850 lines).
- **Zero Behavior Change:** All route paths, HTTP status codes, headers, cookie handlings, and error paths must remain unchanged.
- **100% Backward Compatibility:** Any external import (`import views`, `from views import ...`, `import dash_proxy`, `from dash_proxy import ...`) and unittest patch (`patch("views.resolve_b23_target")`, `patch.object(dash_proxy, "...")`) must function without modification.
- **Route Attachment Preservation:** Views and manifest routes attached to `app` remain directly on `app`; proxy routes registered on `dash_proxy_bp` remain on `dash_proxy_bp`.
- **All Tests Pass:** `npm run test` (all 78 unit tests) and `npm run lint:python` (`ruff check python`) must pass with zero warnings or errors.

## Review Focus

1. **Test Mock Patch Sync:** When a test patches an attribute on the `views` or `dash_proxy` facade (e.g. `patch("views.resolve_b23_target")` or `patch.object(dash_proxy, "DASH_BODY_IDLE_TIMEOUT", 0.05)`), functions executing inside submodules must see the patched value rather than an unpatched original.
2. **Circular Import Prevention:** Sibling modules (`views_*.py` or `dash_*.py`) must not create unresolvable circular import cycles.
3. **Shared State Singletons:** In-memory dictionaries and locks (`_mirror_health`, `_mirror_range_shy`, `_download_jobs`, `_download_jobs_lock`, `_download_limiter`) must remain shared singletons referenced across streaming and download modules.
4. **Range / Content-Length Derivation in Streaming:** `dash_stream.py` must correctly import and utilize range helpers from `dash_common.py` without losing 206 / Content-Range synthesis for dash.js chunk requests.
5. **Download Pipeline Integrity:** `dash_download_track.py` and `dash_download_job.py` must interoperate cleanly to handle segmented Range downloads, muxing, job cancellation, and temporary file cleanup.

---

### Task 1: Views Splitting - Home, Space & Live Modules

**Files:**
- Create: `python/views_home.py`
- Create: `python/views_space.py`
- Create: `python/views_live.py`
- Test: `tests/test_b23_routes.py`, `tests/test_convert_view.py`

**Interfaces:**
- Consumes: `python/shared.py`, `python/app.py` (`app`), `python/b23_resolver.py`, `python/api/`
- Produces:
  - `views_home.py`: `home_view`, `zone_id_view`, `search_view`, `history_view`, `convert_view`, `api_convert`, `static_licenses_view`, `resolve_b23_target`
  - `views_space.py`: `space_view`, `space_json_feed`, `author_view`, `read_view`, `_space_data_key`, `_read_space_data`, `_write_space_data`, etc.
  - `views_live.py`: `live_list_view`, `live_room_view`, `live_chat_sse`, `_pick_live_url`, `_live_fetchable_qualities`

- [ ] **Step 1: Create `python/views_home.py` (~350 lines)**
Extract home, zone, search, history, convert, licenses routes and `resolve_b23_target` from `python/views.py`. Ensure routes are registered with `@app.route(...)`.
- [ ] **Step 2: Create `python/views_space.py` (~450 lines)**
Extract space profile, video list, author, read routes and space Redis cache helper functions from `python/views.py`. Ensure routes are registered with `@app.route(...)`.
- [ ] **Step 3: Create `python/views_live.py` (~320 lines)**
Extract live list, live room, and live SSE chat routes and live stream quality selection helpers from `python/views.py`. Ensure routes are registered with `@app.route(...)`.
- [ ] **Step 4: Verify module compilation and line counts**
Run: `python3 -m py_compile python/views_home.py python/views_space.py python/views_live.py && wc -l python/views_home.py python/views_space.py python/views_live.py`
Expected: Each file < 600 lines, compilation succeeds.
- [ ] **Step 5: Commit**
```bash
git add python/views_home.py python/views_space.py python/views_live.py
git commit -m "refactor: extract views_home, views_space, views_live from views"
```

---

### Task 2: Views Splitting - Video & Audio Modules and `views.py` Facade

**Files:**
- Create: `python/views_video.py`
- Create: `python/views_audio.py`
- Modify: `python/views.py`
- Test: `tests/test_b23_routes.py`, `tests/test_convert_view.py`, `tests/test_review_regressions.py`

**Interfaces:**
- Consumes: `python/views_home.py`, `python/views_space.py`, `python/views_live.py`, `python/dash_proxy.py`
- Produces:
  - `views_video.py`: `video_view`, `video_listen_view`, `api_component_player`, `api_component_meta`, `api_component_meta_more`, `api_component_sub_comments`, `_is_final_play_data`, `_resolve_progressive_sources`, `_get_subtitles`
  - `views_audio.py`: `audio_view`, `audio_list_view`
  - `views.py`: Facade module synchronizing attributes with all `views_*.py` submodules

- [ ] **Step 1: Create `python/views_video.py` (~550 lines)**
Extract video detail, listen mode, player component, comment components, and subtitle resolution from `python/views.py`.
- [ ] **Step 2: Create `python/views_audio.py` (~200 lines)**
Extract single song (`audio_view`) and song menu (`audio_list_view`) routes from `python/views.py`.
- [ ] **Step 3: Transform `python/views.py` into a Synchronizing Facade (< 120 lines)**
Implement `FacadeModule` in `python/views.py` which:
  1. Imports all submodules (`views_home`, `views_space`, `views_live`, `views_video`, `views_audio`).
  2. Overrides `__setattr__` to propagate any patched attributes (e.g. `views.resolve_b23_target`, `views.cache_minutes`, `views.render_template_with_theme`, `views.asyncio.gather`) directly into the submodules' `__dict__`.
  3. Re-exports all route functions, helpers, and imported libraries (`comment`, `homepage`, `asyncio`, etc.) to preserve 100% test and application compatibility.
  4. Installs the facade instance into `sys.modules["views"]`.
- [ ] **Step 4: Run test suite for views routes**
Run: `PYTHONPATH=python uv run python -m unittest tests/test_b23_routes.py tests/test_convert_view.py tests/test_review_regressions.py`
Expected: PASS (all tests pass, including patches on `views.*`).
- [ ] **Step 5: Verify line counts and ruff linting**
Run: `wc -l python/views*.py && uv run ruff check python/views*.py`
Expected: Every `views_*.py` and `views.py` is < 600 lines, ruff check passes cleanly.
- [ ] **Step 6: Commit**
```bash
git add python/views_video.py python/views_audio.py python/views.py
git commit -m "refactor: complete views modularization with synchronizing facade"
```

---

### Task 3: Dash Splitting - Common, Fetch & MPD Modules

**Files:**
- Create: `python/dash_common.py`
- Create: `python/dash_fetch.py`
- Create: `python/dash_mpd.py`
- Test: `tests/test_review_regressions.py`

**Interfaces:**
- Consumes: `python/shared.py`, `python/api/`
- Produces:
  - `dash_common.py`: Constants, `_mirror_health`, `_mirror_range_shy`, `_note_mirror_ok`, `_note_mirror_bad`, `_is_safe_dash_url`, `_build_dash_cdn_headers`, `_parse_content_range_full`, `_parse_416_total`, `_shift_dash_range`, `_expected_resume_start`, `_TrackCut`, `_CUT_ERRORS`, etc.
  - `dash_fetch.py`: `video_get_dash_for_qn`, `_load_dash_data`, `_refresh_dash_data`, `_lookup_track`, `has_valid_dash_tracks`, `fetch_durl_supported_src`, `_select_durl_results`, etc.
  - `dash_mpd.py`: `generate_vod_mpd`, `_sb_initialization_range`, `video_dash_manifest_view`

- [ ] **Step 1: Create `python/dash_common.py` (~450 lines)**
Extract common constants, mirror tracking dicts and methods, URL security checks, CDN headers generation, range calculation helpers, and track download exceptions from `python/dash_proxy.py`.
- [ ] **Step 2: Create `python/dash_fetch.py` (~700 lines)**
Extract playurl resolution, caching, PGC/UGC playurl parsing, staleness checks, track lookup (`_lookup_track`), and progressive durl fallback logic from `python/dash_proxy.py`.
- [ ] **Step 3: Create `python/dash_mpd.py` (~250 lines)**
Extract MPD generation logic and `video_dash_manifest_view` route from `python/dash_proxy.py`. Ensure manifest route attaches to `app` as `@app.route("/video/dash/<vid>/<int:idx>/manifest.mpd")`.
- [ ] **Step 4: Verify module compilation and line counts**
Run: `python3 -m py_compile python/dash_common.py python/dash_fetch.py python/dash_mpd.py && wc -l python/dash_common.py python/dash_fetch.py python/dash_mpd.py`
Expected: Each file < 750 lines, compilation succeeds.
- [ ] **Step 5: Commit**
```bash
git add python/dash_common.py python/dash_fetch.py python/dash_mpd.py
git commit -m "refactor: extract dash_common, dash_fetch, dash_mpd"
```

---

### Task 4: Dash Splitting - Stream Proxy Module

**Files:**
- Create: `python/dash_stream.py`
- Test: `tests/test_review_regressions.py`

**Interfaces:**
- Consumes: `python/dash_common.py`, `python/dash_fetch.py`
- Produces:
  - `dash_stream.py`: `proxy_dash`, `_fetch_dash_track`, `_failover_dash_conn`, `_yield_dash_body`, `_ResumedDashConn`, `_SlowDashBody`, `_DashUpstreamError`, `_retry_proxy_with_fresh_playurl`, etc.

- [ ] **Step 1: Create `python/dash_stream.py` (~800 lines)**
Extract live DASH fragment proxying, connection failover, handshake, idle watchdog, and `proxy_dash` route handler from `python/dash_proxy.py`.
Import required constants and helpers from `dash_common.py` and `dash_fetch.py`.
- [ ] **Step 2: Verify module compilation and line count**
Run: `python3 -m py_compile python/dash_stream.py && wc -l python/dash_stream.py`
Expected: File line count < 850 lines, compilation succeeds.
- [ ] **Step 3: Commit**
```bash
git add python/dash_stream.py
git commit -m "refactor: extract dash_stream proxying module"
```

---

### Task 5: Dash Splitting - Download Stack & `dash_proxy.py` Facade

**Files:**
- Create: `python/dash_download_track.py`
- Create: `python/dash_download_job.py`
- Modify: `python/dash_proxy.py`
- Test: `tests/test_review_regressions.py`

**Interfaces:**
- Consumes: `python/dash_common.py`, `python/dash_fetch.py`, `python/dash_stream.py`
- Produces:
  - `dash_download_track.py`: `_download_track_segmented`, `_download_track_file`, `_download_track_to_file`, `_mux_tracks`, `_open_cdn_track`, etc.
  - `dash_download_job.py`: `_DownloadJob`, `_download_jobs`, `create_download_job`, `cancel_download_job`, `download_status`, `download_file`, `download_cancel`, legacy `proxy_download`, etc.
  - `dash_proxy.py`: Facade module defining `dash_proxy_bp`, registering routes, and synchronizing attributes with submodules.

- [x] **Step 1: Create `python/dash_download_track.py` (~750 lines)**
Extract segmented track downloader, body streaming, resume retries, and ffmpeg muxing from `python/dash_proxy.py`.
- [x] **Step 2: Create `python/dash_download_job.py` (~800 lines)**
Extract download job registry, concurrency limiter, job runners, status/file/cancel endpoints, and legacy streaming download route from `python/dash_proxy.py`.
- [x] **Step 3: Transform `python/dash_proxy.py` into a Synchronizing Facade (< 150 lines)**
Implement `dash_proxy.py` facade which:
  1. Defines `dash_proxy_bp = Blueprint("dash_proxy", __name__)`.
  2. Registers all routes on `dash_proxy_bp` (`proxy_dash`, `download_status`, `download_file`, `download_cancel`, `proxy_download`).
  3. Re-exports all symbols from `dash_common`, `dash_fetch`, `dash_mpd`, `dash_stream`, `dash_download_track`, and `dash_download_job`.
  4. Overrides `__setattr__` to propagate any patched attributes (e.g. `_mirror_health`, `_mirror_range_shy`, `DASH_BODY_IDLE_TIMEOUT`, `_fetch_dash_attempt`) into the submodules.
  5. Installs the facade instance into `sys.modules["dash_proxy"]`.
- [x] **Step 4: Run unit tests for dash proxy and regressions**
Run: `PYTHONPATH=python uv run python -m unittest tests/test_review_regressions.py`
Expected: PASS (all regression tests pass, including stream body watchdog, failover conn, and mirror shy tests).
- [x] **Step 5: Verify line counts and ruff linting**
Run: `wc -l python/dash_*.py && uv run ruff check python/dash_*.py`
Expected: Every `dash_*.py` is < 850 lines, ruff check passes.
- [x] **Step 6: Commit**
```bash
git add python/dash_download_track.py python/dash_download_job.py python/dash_proxy.py
git commit -m "refactor: complete dash_proxy modularization with synchronizing facade"
```

---

### Task 6: Full Verification, Static Checks & Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-10-28-split-dash-proxy-and-views-design.md` (if any final notes)
- Test: All test files in `tests/`

- [x] **Step 1: Check line counts across ALL Python files**
Run: `wc -l python/*.py`
Verify that NO file exceeds 1,000 lines.
- [x] **Step 2: Run complete test suite**
Run: `npm run test`
Expected: Ran 78 tests, all OK (exit code 0).
- [x] **Step 3: Run project Python linter**
Run: `npm run lint:python`
Expected: `All checks passed!` with 0 errors.
- [x] **Step 4: Run full compile check**
Run: `npm run check:compile`
Expected: 0 syntax errors or unhandled compile issues.
- [x] **Step 5: Commit final verification**
```bash
git add python/ docs/
git commit -m "chore: verify file length ceiling and test suite integrity"
```
