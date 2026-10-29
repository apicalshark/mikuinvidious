# Rust Core Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the native Rust extension `crates/miku_core_rs` (PyO3 0.30+, Stable ABI `abi3-py315`) to accelerate CPU-intensive tasks—BV/AV ID conversion, DASH MPD XML manifest generation, comment emote & search text processing, and live WebSocket packet unpacking & Brotli decompression—with 100% transparent pure-Python fallbacks.

**Architecture:**
1. **PyO3 Core (`crates/miku_core_rs`)**: Implement dedicated Rust submodules (`bvid`, `mpd`, `text`, `live`) using zero-copy algorithms, `quick-xml`, `serde_json`, and `brotli`. Detach GIL (`py.allow_threads`) during CPU-bound decompression and string formatting.
2. **Transparent Fallbacks (`python/`)**: Keep existing pure-Python implementations as robust fallback paths. When `miku_core_rs` is installed, route requests to native bindings; otherwise gracefully fall back with zero breaking changes or runtime penalties.
3. **Parity & Benchmarking**: Provide dual-mode unit tests validating exact output equivalence between Rust and Python implementations, and extend benchmarking tooling to track throughput improvements.

**Tech Stack:** Python 3.15+, Rust 2021 (PyO3 0.30.1, `abi3-py315`, Maturin 1.15.0), `quick-xml 0.42`, `brotli 7.0`, `serde_json 1.0`, `serde 1.0`.

**Spec:** Brainstorming discussion on high-impact CPU hot-paths across playback, stream proxy, comment rendering, and live danmaku.

## Global Constraints

- Verification MUST use `npm test`, `npm run lint`, and `npm run check:compile`. Never invoke ruff, djlint, or cargo directly without the project npm scripts.
- Rust extensions MUST support optional compilation / graceful pure-Python fallback so environments without `cargo` still run tests and server without crashing.
- Python code MUST target Python 3.15+; Rust code MUST target the 2021/2024 edition and PyO3 `abi3-py315` Stable ABI.
- Native Rust implementations MUST release the GIL (`py.allow_threads` / `py.detach`) on heavy compute paths (XML generation, Brotli decompression, parsing loops).
- Return shapes, dict keys, and exception semantics in Rust MUST match pure-Python counterparts exactly.

## Review Focus

1. **Brotli Truncated / Corrupt Live Data**: Live WebSocket streams occasionally cut packets mid-frame. Rust decompression must not panic on malformed bytes; it must handle errors gracefully and mirror Python fallback behavior.
2. **DASH MPD AdaptationSet Codec Grouping**: Bilibili returns multiple codecs (AVC/HEVC/AV1) across quality ladders. Grouping by `codecid` in Rust must match `dash_mpd.py` track ordering and initialization ranges exactly to prevent player MSE decode errors.
3. **Emote HTML Sanitization & Attribute Breakout**: Emote tokens and messages contain arbitrary user inputs. The Rust tokenizer and HTML escaping must guarantee zero XSS injection and enforce allowed CDN domains (`_REPLY_EMOTE_DOMAINS`).
4. **WBI / BV2AV Edge Cases**: Invalid BV strings (missing "BV" prefix, wrong length, non-base58 chars) and negative/zero AIDs must raise identical exceptions (`ArgsException` / `ValueError`) in both Rust and Python.
5. **Memory Overhead & Leak Protection**: High-frequency MPD manifest generation and live packet unpacking must reuse buffers and avoid unbounded string cloning or memory leaks in PyO3 reference conversions.

---

### Task 1: BV/AV ID Conversion in Rust with Pure-Python Fallback

**Files:**
- Create: `crates/miku_core_rs/src/bvid.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/api/video.py:40-76`
- Create: `tests/test_bvid_rs.py`

**Interfaces:**
- Consumes: Standard PyO3 types
- Produces: `miku_core_rs.bv2av(bvid: str) -> int`, `miku_core_rs.av2bv(aid: int) -> str`

- [x] **Step 1: Write the failing unit test**

Create `tests/test_bvid_rs.py` asserting:
1. `bv2av("BV1xx411c7xx")` produces identical integers in Python vs Rust.
2. `av2bv(170001)` produces identical BV strings in Python vs Rust.
3. Invalid BV formats (e.g. wrong prefix, illegal chars, wrong length) raise `ValueError` (or `ArgsException`).
4. Aid <= 0 raises `ValueError` (or `ArgsException`).

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_bvid_rs.py`
Expected: FAIL with `AttributeError` or `ImportError: cannot import name 'bv2av' from 'miku_core_rs'`

- [x] **Step 3: Implement `crates/miku_core_rs/src/bvid.rs`**

Implement Base58 table permutation, XOR, and mask logic in Rust:
```rust
pub fn bv2av(bvid: &str) -> PyResult<i64>
pub fn av2bv(aid: i64) -> PyResult<String>
```
Export them in `crates/miku_core_rs/src/lib.rs`.

- [x] **Step 4: Update `python/api/video.py` to use `miku_core_rs` with Python fallback**

```python
try:
    from miku_core_rs import av2bv as _rs_av2bv, bv2av as _rs_bv2av
except ImportError:
    _rs_av2bv = None
    _rs_bv2av = None
```
Wrap `bv2av` and `av2bv` so they call the Rust implementations when available, translating exceptions cleanly to `ArgsException`.

- [x] **Step 5: Build Rust extension and run test**

Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_bvid_rs.py`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add crates/miku_core_rs/src/bvid.rs crates/miku_core_rs/src/lib.rs python/api/video.py tests/test_bvid_rs.py
git commit -m "feat(core): accelerate bv2av and av2bv with rust"
```
*(Completed in commit `3ddbe96`)*

---

### Task 2: DASH MPD Manifest Generation in Rust

**Files:**
- Create: `crates/miku_core_rs/src/mpd.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/dash_mpd.py:37-160`
- Create: `tests/test_mpd_rs.py`

**Interfaces:**
- Consumes: `dash_data` dictionary (or JSON string), `vid: str`, `idx: int`
- Produces: `miku_core_rs.generate_vod_mpd(vid: str, idx: int, dash_data: dict) -> str | None`

- [x] **Step 1: Write the failing unit test**

Create `tests/test_mpd_rs.py` with representative fixture data containing video tracks (AVC, HEVC, AV1) and audio tracks:
1. Verify `generate_vod_mpd` output from Rust matches Python `generate_vod_mpd` structure.
2. Verify invalid/missing SegmentBase or empty tracks return None or valid minimal MPD without panicking.
3. Verify test runs against both native Rust and pure Python fallback.

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_mpd_rs.py`
Expected: FAIL with `cannot import name 'generate_vod_mpd' from 'miku_core_rs'`

- [x] **Step 3: Implement `crates/miku_core_rs/src/mpd.rs`**

Implement XML manifest builder in Rust:
- Extract `duration`, `minBufferTime`.
- Group video tracks by `codecid` using an ordered map / vector preserving order.
- Format `AdaptationSet` nodes for video and audio.
- Generate Representations with `<BaseURL>/proxy/dash/{vid}/{idx}/{type}/{qn}/{cid}</BaseURL>` and `SegmentBase` index ranges.
- Release GIL (`py.allow_threads`) during string construction.
Export in `crates/miku_core_rs/src/lib.rs`.

- [x] **Step 4: Update `python/dash_mpd.py` to prioritize Rust implementation**

Import `generate_vod_mpd as rs_generate_vod_mpd` from `miku_core_rs`.
In `generate_vod_mpd(vid, idx, dash_data)`:
```python
if rs_generate_vod_mpd is not None:
    try:
        mpd = rs_generate_vod_mpd(str(vid), int(idx), dash_data)
        if mpd is not None:
            return mpd
    except Exception:
        pass
# Fall back to pure Python implementation
```

- [x] **Step 5: Run tests and verify**

Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_mpd_rs.py`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add crates/miku_core_rs/src/mpd.rs crates/miku_core_rs/src/lib.rs python/dash_mpd.py tests/test_mpd_rs.py
git commit -m "feat(core): accelerate dash mpd generation with rust"
```
*(Completed in commit `953d877`)*

---

### Task 3: Comment Emote Rendering & Search Highlight Stripping in Rust

**Files:**
- Create: `crates/miku_core_rs/src/text.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/transformers.py:22-43,301-345`
- Create: `tests/test_text_rs.py`

**Interfaces:**
- Consumes: `message: str`, `emote: dict`, `text: str`
- Produces:
  - `miku_core_rs.strip_search_highlight(text: str) -> str`
  - `miku_core_rs.render_reply_content(message: str, emote: dict | None) -> str`

- [x] **Step 1: Write the failing unit test**

Create `tests/test_text_rs.py`:
1. Test `strip_search_highlight`: removes `<em class="keyword">...</em>` and unescapes entities (`&amp;` -> `&`, `&lt;` -> `<`).
2. Test `render_reply_content`: replaces valid `[...]` tokens with `<img ...>` for allowed domains, rejects non-allowed domains, escapes surrounding HTML, handles size 1 vs size 2 styles.
3. Compare Rust output against existing Python implementation from `tests/test_reply_emoji.py`.

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_text_rs.py`
Expected: FAIL with `cannot import name 'strip_search_highlight' from 'miku_core_rs'`

- [x] **Step 3: Implement `crates/miku_core_rs/src/text.rs`**

- `strip_search_highlight`: strip `</?em[^>]*>` and decode standard HTML entities.
- `render_reply_content`: parse bracket tokens `[...]`, lookup token in `emote` PyDict, check allowed domains (`.hdslb.com`, `.biliimg.com`, etc.), escape HTML, and format `<img>` tags.
- Export in `crates/miku_core_rs/src/lib.rs`.

- [x] **Step 4: Update `python/transformers.py`**

Import native functions from `miku_core_rs` with pure-Python fallbacks.

- [x] **Step 5: Run tests and verify**

Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_text_rs.py tests/test_reply_emoji.py`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add crates/miku_core_rs/src/text.rs crates/miku_core_rs/src/lib.rs python/transformers.py tests/test_text_rs.py
git commit -m "feat(core): accelerate text stripping and emote rendering with rust"
```
*(Completed in commit `e27ef80`)*

---

### Task 4: Live Danmaku Binary Protocol Parser & Brotli Decompression in Rust

**Files:**
- Modify: `crates/miku_core_rs/Cargo.toml` (add `brotli = "7.0"`, `serde_json = "1.0"`, `serde = { version = "1.0", features = ["derive"] }`)
- Create: `crates/miku_core_rs/src/live.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/api/live.py:440-495`
- Create: `tests/test_live_rs.py`

**Interfaces:**
- Consumes: Raw `data: bytes`
- Produces: `miku_core_rs.unpack_live_danmaku(data: &[u8]) -> PyResult<Vec<PyObject>>`

- [x] **Step 1: Write the failing unit test**

Create `tests/test_live_rs.py`:
1. Craft synthetic packets for Heartbeat Response (`protover=1, type=3`).
2. Craft synthetic packets for Verify Success Response (`type=8`) with JSON payload.
3. Craft synthetic Brotli-compressed packets (`protover=3`) carrying multiple inner packets.
4. Verify parsed structure matches `LiveDanmaku._unpack(data)` exactly.
5. Verify corrupted Brotli or truncated packets do not panic and fail gracefully.

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_live_rs.py`
Expected: FAIL with `cannot import name 'unpack_live_danmaku' from 'miku_core_rs'`

- [x] **Step 3: Update `Cargo.toml` and implement `crates/miku_core_rs/src/live.rs`**

Add dependencies to `Cargo.toml`:
```toml
brotli = "7.0"
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
```
In `crates/miku_core_rs/src/live.rs`:
- Read 16-byte big-endian header (`>IHHII`).
- Decompress Brotli stream if `protover == 3` inside `py.allow_threads`.
- Iterate inner packets and deserialize JSON or view counts.
- Convert records to Python dictionaries (`PyDict`).
- Export in `crates/miku_core_rs/src/lib.rs`.

- [x] **Step 4: Update `python/api/live.py`**

In `LiveDanmaku._unpack`:
```python
try:
    from miku_core_rs import unpack_live_danmaku as _rs_unpack
except ImportError:
    _rs_unpack = None
```
If `_rs_unpack` is available, delegate unpacking to Rust; fallback to pure Python `struct`/`brotli`/`json` logic on exception or when missing.

- [x] **Step 5: Run tests and verify**

Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_live_rs.py`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add crates/miku_core_rs/Cargo.toml crates/miku_core_rs/Cargo.lock crates/miku_core_rs/src/live.rs crates/miku_core_rs/src/lib.rs python/api/live.py tests/test_live_rs.py
git commit -m "feat(core): accelerate live danmaku binary parsing and brotli decompression in rust"
```
*(Completed in commit `b12387e`)*

---

### Task 5: Comprehensive Benchmark Suite & Quality Assurance

**Files:**
- Create/Modify: `tools/benchmark_core.py` (incorporating danmaku, wbi, bvid, mpd, text, live)
- Modify: `package.json` (add/update benchmark scripts if needed)

**Interfaces:**
- Consumes: Native Rust extension and pure Python fallbacks
- Produces: Benchmark report measuring speedups (items/sec and microsecond latency)

- [x] **Step 1: Create `tools/benchmark_core.py`**

Benchmark each subsystem:
1. Danmaku XML parsing (existing)
2. WBI signing (existing)
3. BV2AV / AV2BV conversion
4. DASH MPD generation (1,000 iterations over 4K/1080P multi-codec playurl JSON)
5. Reply emote rendering (5,000 iterations over comment strings with mixed emotes)
6. Live danmaku Brotli packet unpack (1,000 iterations over multi-packet payloads)

- [x] **Step 2: Run benchmark tool to verify speedups**

Run: `PYTHONPATH=python uv run python tools/benchmark_core.py`
Expected: All benchmarks run cleanly and report speedup multipliers (>2x to >15x faster).

- [x] **Step 3: Run comprehensive project verification**

Run:
1. `npm run check:compile` (Verify Python bytecode compilation across python, tests, tools)
2. `npm test` (Verify all 80+ tests pass in both native and fallback modes)
3. `npm run lint` (Verify Ruff, Prettier, and Rust Clippy / rustfmt pass cleanly)

- [x] **Step 4: Commit**

```bash
git add tools/benchmark_core.py package.json
git commit -m "chore(benchmark): add comprehensive benchmark suite for rust core acceleration"
```
*(Completed in commit `c7589c4`)*
