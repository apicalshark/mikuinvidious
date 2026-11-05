# Design & Implementation Plan: LiveChat Anti-Abuse Handshake Gate & Room Multiplexer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate server resource exhaustion, upstream Bilibili WebSocket churn, and stdout log flooding when scanners, bots, or unstable clients rapidly connect and disconnect from `/live/chat/<int:room_id>`.

**Architecture:** Combine a two-layer defense in `python/views_live.py`:
1. **Layer 1 (Handshake Gate):** On SSE connection start, yield the initial greeting frame and wait `LIVE_CHAT_HANDSHAKE_DELAY` (1.0s) before subscribing to the room hub. Immediate TCP disconnects (`ASGI transport error: "Closed(..)"`) terminate before allocating room state or dialing Bilibili.
2. **Layer 2 (`LiveChatManager` Room Multiplexer):** All clients watching the same `room_id` share a single `LiveChatRoom` and a single upstream `b_live.LiveDanmaku` WebSocket connection. Client departures simply remove their bounded `asyncio.Queue` from the room's subscriber set without interrupting the upstream connection. When the last subscriber leaves, an idle grace timer (`LIVE_CHAT_IDLE_GRACE = 30.0s`) keeps the upstream connection warm for reconnects before shutting down cleanly.

**Tech Stack:** Python 3.15+, Quart, `asyncio`, `orjson`, local `api.live.LiveDanmaku`.

---

## Global Constraints

- Preserve existing SSE wire format (`data: {"user":"SYSTEM","text":"Chat connected"}\n\n`, `data: {"user":...,"text":...}\n\n`, `: heartbeat\n\n`).
- Zero per-client background tasks: use `asyncio.wait_for(queue.get(), timeout=LIVE_CHAT_HEARTBEAT_INTERVAL)` instead of spawning a dedicated heartbeat task per client.
- Bounded per-client queue (`LIVE_CHAT_QUEUE_MAXSIZE = 100`) with oldest-message eviction so stalled clients cannot cause memory growth.
- Maintain `views.py` facade compatibility.
- Pass `ruff check` and the full unit test suite.

## Review Focus

1. **Immediate Scanner Disconnects:** A client disconnecting during `LIVE_CHAT_HANDSHAKE_DELAY` must never instantiate `LiveDanmaku` or create a `LiveChatRoom`.
2. **Upstream Sharing Across Concurrent Clients:** Two or more clients connected to the same `room_id` must trigger only one `LiveDanmaku(room_id)` connection and both receive broadcast `DANMU_MSG` events.
3. **Churn Immunity During Idle Grace:** A client disconnecting and reconnecting within `LIVE_CHAT_IDLE_GRACE` must reuse the active `LiveChatRoom` without disconnecting `LiveDanmaku`.
4. **Idle Cleanup:** Once all subscribers leave and `LIVE_CHAT_IDLE_GRACE` elapses, `LiveDanmaku.disconnect()` must be called and the room removed from `LiveChatManager`.
5. **Backpressure Safety:** If a client stops draining its queue while danmaku events arrive beyond `LIVE_CHAT_QUEUE_MAXSIZE`, `broadcast()` must evict the oldest item without blocking or raising `QueueFull`.

---

## Approaches Considered

1. **Room-Level Multiplexer (`LiveChatManager`):** Matches `python/live_manager.py` by sharing one upstream connection per room with an idle grace period.
2. **Handshake-Gated Upstream Dialing:** Delays upstream subscription until the client survives an initial handshake window.
3. **Combined Defense-in-Depth (Selected):** Uses the Handshake Gate in front of `LiveChatManager` so instant TCP drops cost zero room/upstream state, while persistent or concurrent clients share one resilient upstream connection.

---

## Implementation Tasks

### Task 1: Write Failing Unit Tests for Handshake Gate and Room Multiplexer

**Files:**
- Modify: `tests/test_live.py`

**Interfaces:**
- Consumes: `views_live.LiveChatManager`, `views_live.LiveChatRoom`, `views_live.live_chat_manager`, `views_live.LIVE_CHAT_HANDSHAKE_DELAY`, `views_live.LIVE_CHAT_IDLE_GRACE`, `views_live.LIVE_CHAT_HEARTBEAT_INTERVAL`, `views_live.LIVE_CHAT_QUEUE_MAXSIZE`

- [x] **Step 1: Add unit tests to `tests/test_live.py`**
  - `test_probe_disconnect_before_handshake_does_not_connect_upstream`
  - `test_multiple_clients_share_single_upstream_danmaku_and_receive_broadcast`
  - `test_client_disconnect_within_grace_keeps_upstream_alive`
  - `test_idle_grace_expiry_disconnects_upstream_and_removes_room`
  - `test_bounded_subscriber_queue_drops_oldest_on_overflow`

- [x] **Step 2: Run `tests/test_live.py` and verify RED failure**

### Task 2: Implement `LiveChatRoom`, `LiveChatManager`, and Updated `live_chat_sse`

**Files:**
- Modify: `python/views_live.py:25-118`

**Interfaces:**
- Produces:
  - `LIVE_CHAT_HANDSHAKE_DELAY = 1.0`
  - `LIVE_CHAT_IDLE_GRACE = 30.0`
  - `LIVE_CHAT_HEARTBEAT_INTERVAL = 20.0`
  - `LIVE_CHAT_QUEUE_MAXSIZE = 100`
  - `LiveChatRoom(room_id: int, manager: LiveChatManager, idle_grace: float = LIVE_CHAT_IDLE_GRACE)`
  - `LiveChatManager()`
  - `live_chat_manager = LiveChatManager()`

- [x] **Step 1: Implement `LiveChatRoom` and `LiveChatManager` in `python/views_live.py`**
- [x] **Step 2: Update `live_chat_sse` to apply the handshake delay, subscribe/unsubscribe via `live_chat_manager`, and emit heartbeats on queue timeout**
- [x] **Step 3: Run `tests/test_live.py` and verify GREEN pass**
- [x] **Step 4: Run full test suite and `ruff check`**
