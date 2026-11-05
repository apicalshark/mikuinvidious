# Implementation Plan: Endpoint Anti-Abuse Hardening & Danmaku XML Caching

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate server resource exhaustion, orphan `ffmpeg` processes on client disconnect, uncached upstream danmaku XML fetches, and unthrottled Bilibili WBI API waterfalls across all remaining vulnerable endpoints.

**Architecture:**
1. **Download Cancellation & Tightened Rate Limit (`dash_download_track.py`, `dash_download_job.py`):** Tighten `GET /proxy/download/<vid>/<idx>/<qual>` from `RATE_LIMITS["proxy"]` (100/min) to `RATE_LIMITS["strict"]` (10/min), wire a per-request `cancel_event` into `_download_track_file`, kill the `ffmpeg` subprocess in `_mux_tracks` when `asyncio.CancelledError` fires (client TCP disconnect), and shield `shutil.rmtree(tmpdir)` cleanup.
2. **Danmaku XML Caching & Validation (`res.py`):** Add `@rate_limit(**RATE_LIMITS["normal"])` and `_is_valid_vid` validation to `/res/danmaku/<vid>` (`:<idx>`), and cache valid upstream XML in Redis under `miku_danmaku_xml_{vid}_{idx}` for `DANMAKU_CACHE_TTL = 900` seconds (15 minutes) with fail-open Redis reads/writes.
3. **Live Room Caching & Rate Limiting (`views_live.py`):** Add `@rate_limit(**RATE_LIMITS["normal"])` to `/live` and `/live/<int:room_id>`, and cache resolved room metadata + playback sources in Redis under `live:room:{room_id}` (`LIVE_ROOM_CACHE_TTL = 60` seconds) so burst hits do not trigger sequential WBI play-info waterfalls.
4. **Video Listen Cache Sharing & Rate Limiting (`views_video.py`):** Add `@rate_limit(**RATE_LIMITS["normal"])` to `/video_listen/<vid>` and read/populate the shared `video:data:{vid}:{idx}` Redis cache.
5. **View Route Rate Limits (`views_space.py`, `views_audio.py`, `views_home.py`):** Apply `@rate_limit(**RATE_LIMITS["normal"])` to `/space/<mid>`, `/space/<mid>/json`, `/author/<mid>`, `/read/<cid>`, `/opus/<cid>`, `/audio/<auid>`, `/audio_list/<amid>`, `/`, and `/vv/<zid>`.

**Tech Stack:** Python 3.15+, Quart, Redis (`appredis`), `asyncio`, `unittest`.

---

## Global Constraints

- Redis operations must fail open (`try ... except Exception`) so an unreachable Redis never breaks danmaku or page rendering.
- HTML block pages (`<!doctype` / `<html`) from upstream risk control must never be cached as danmaku XML.
- Preserve existing response formats and status codes.
- Pass `ruff check`, `ruff format --check` on modified files, and unit tests.

## Review Focus

1. **Orphan `ffmpeg` Prevention:** Cancelling `_mux_tracks` via `asyncio.CancelledError` must kill the spawned `ffmpeg` subprocess and wait for it to exit.
2. **Temp Dir Cleanup on Disconnect:** Cancelling `proxy_download` mid-download must remove `tmpdir` via shielded cleanup.
3. **Danmaku XML Cache Hit/Miss & Poisoning Guard:** Second request for `/res/danmaku/<vid>:<idx>` serves from Redis without calling `Video.get_danmaku_xml()`; HTML block responses are rejected and not cached.
4. **Live Room Waterfall Shield:** Second request for `/live/<room_id>` within TTL serves from `live:room:<room_id>` cache without calling `LiveRoom.get_room_info()`.
5. **Video Listen Cache Reuse:** When `video:data:<vid>:<idx>` is cached, `/video_listen/<vid>` reuses `vinfo`, `vtags`, `vrelated`, and `vset` without calling `v.get_info()`.

---

## Tasks

### Task 1: Harden `/proxy/download` Against Disconnects and Abuse

**Files:**
- Modify: `python/dash_download_track.py:387-416`
- Modify: `python/dash_download_job.py:91-235`
- Test: `tests/test_live.py` (or `tests/test_danmaku.py` / dedicated test class)

- [x] **Step 1: Write failing tests for `_mux_tracks` killing `ffmpeg` on `asyncio.CancelledError` and `proxy_download` cleaning up `tmpdir` on cancellation**
- [x] **Step 2: Implement cancellation handling in `_mux_tracks` and `proxy_download` + `@rate_limit(**RATE_LIMITS["strict"])`**

### Task 2: Cache Danmaku XML and Rate-Limit `/res/danmaku`

**Files:**
- Modify: `python/res.py:59-81`
- Test: `tests/test_danmaku.py`

- [x] **Step 1: Write failing tests in `tests/test_danmaku.py` for Redis XML caching, invalid `vid` rejection, and HTML block-page rejection**
- [x] **Step 2: Implement `DANMAKU_CACHE_TTL = 900`, `_is_valid_vid(vid)` check, `@rate_limit(**RATE_LIMITS["normal"])`, and Redis `miku_danmaku_xml_{vid}_{idx}` caching in `python/res.py`**

### Task 3: Rate-Limit and Cache `/live/<room_id>`, `/video_listen/<vid>`, and View Routes

**Files:**
- Modify: `python/views_live.py`
- Modify: `python/views_video.py`
- Modify: `python/views_space.py`
- Modify: `python/views_audio.py`
- Modify: `python/views_home.py`
- Test: `tests/test_live.py`

- [x] **Step 1: Write failing tests for `/live/<room_id>` cache hit avoiding upstream calls, `/video_listen/<vid>` reusing `video:data:<vid>:<idx>`, and rate-limit enforcement on protected routes**
- [x] **Step 2: Implement caching and `@rate_limit(**RATE_LIMITS["normal"])` across the view modules**
- [x] **Step 3: Run tests and linter checks**
