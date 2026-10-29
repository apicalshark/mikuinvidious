# Refactor Rust Core with Idiomatic Crates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `crates/miku_core_rs` to replace all manual and ad-hoc string parsing, URL inspection, XML string building, and binary slicing with established, idiomatic Rust crates (`dash-mpd`, `url`, `regex`, and `byteorder`).

**Architecture:** Use `dash-mpd` (`default-features = false`) to build strongly-typed MPEG-DASH manifest data models serialized via `mpd.to_string()`; use `url` for WHATWG-compliant URL parsing and security validation; use `regex` with `std::sync::LazyLock` for pre-compiled regular expressions; use `byteorder` for type-safe big-endian live danmaku binary packet unpacking. Keep all Python C-extension ABI contracts, function signatures, and pure-Python fallbacks identical.

**Tech Stack:** Rust 2021 (rustc 1.99+), PyO3 0.30+, `dash-mpd 0.21` (no default features), `url 2.5`, `regex 1.11`, `byteorder 1.5`, `brotli 7.0`, `serde_json 1.0`, Python 3.15.

**Spec:** Refactor `miku_core_rs` to use standard crates from crates.io instead of manual/hacky code, preserving 100% test compatibility and performance.

## Global Constraints

- Pure-Python fallback behavior and output format must remain 100% semantically and functionally identical.
- Target Stable ABI `abi3-py315` via PyO3 0.30+.
- All 111 unit tests in `npm test` must continue to pass.
- `cargo clippy --all-targets -- -D warnings` and `cargo fmt` must pass without warnings.
- GIL must be released (`py.detach`) on compute-heavy operations.

## Review Focus

1. **`dash-mpd` XML Output**: The generated MPD XML must include all required attributes (`profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"`, `minBufferTime`, `mediaPresentationDuration`, `SegmentBase.indexRange`, `Initialization.range`, `BaseURL`) and be parseable by DASH video players (`dash.js`) and `xml.dom.minidom`.
2. **URL Normalization**: `proxied_emote_src` must safely handle scheme-relative URLs (`//i0.hdslb.com/...`), absolute URLs (`http://`, `https://`), and reject dot-segment escapes (`..`) and untrusted domains without throwing exceptions.
3. **ReDoS & Performance**: Regexes in `text.rs` must be initialized once via `std::sync::LazyLock` to avoid repeated compilation overhead.
4. **Binary Protocol Robustness**: Live danmaku unpacking must gracefully handle truncated packets (<16 bytes) and corrupt Brotli data without panicking.
5. **XML Character Escaping**: User comment text inside `render_reply_content` must escape `<`, `>`, `&`, `"`, `'` matching Python's `html.escape(..., quote=True)` to prevent XSS.

---

### Task 1: Add Dependencies to `crates/miku_core_rs/Cargo.toml`

**Files:**
- Modify: `crates/miku_core_rs/Cargo.toml`

**Interfaces:**
- Produces: `dash-mpd`, `url`, `regex`, and `byteorder` available in `miku_core_rs`.

- [ ] **Step 1: Update Cargo.toml dependencies**
Add the following to `crates/miku_core_rs/Cargo.toml`:
```toml
dash-mpd = { version = "0.21", default-features = false }
url = "2.5"
regex = "1.11"
byteorder = "1.5"
```

- [ ] **Step 2: Verify compilation with cargo check**
Run: `cargo check --manifest-path crates/miku_core_rs/Cargo.toml`
Expected: downloads and compiles crates with 0 errors.

- [ ] **Step 3: Commit**
```bash
git add crates/miku_core_rs/Cargo.toml crates/miku_core_rs/Cargo.lock
git commit -m "chore(core): add dash-mpd, url, regex, and byteorder crates"
```

---

### Task 2: Refactor Text & URL Processing with `url` and `regex` in `crates/miku_core_rs/src/text.rs`

**Files:**
- Modify: `crates/miku_core_rs/src/text.rs`
- Test: `tests/test_text_rs.py`, `tests/test_reply_emoji.py`

**Interfaces:**
- Consumes: `url::Url`, `regex::Regex`, `std::sync::LazyLock`.
- Produces:
  - `strip_search_highlight(text: &str) -> String`
  - `proxied_emote_src(raw_url: &str) -> Option<String>`
  - `render_reply_content(message: &str, emote: Option<&Bound<'_, PyDict>>) -> PyResult<String>`

- [ ] **Step 1: Write static regexes in `text.rs`**
Define `SEARCH_HIGHLIGHT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)</?em[^>]*>").unwrap());` and `REPLY_EMOTE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[([^\[\]\n\r]{1,50})\]").unwrap());`.

- [ ] **Step 2: Implement `strip_search_highlight` using `SEARCH_HIGHLIGHT_RE`**
Replace manual loop with `SEARCH_HIGHLIGHT_RE.replace_all(text, "")` and decode HTML entities with `html_escape::decode_html_entities`.

- [ ] **Step 3: Implement `proxied_emote_src` using `url::Url`**
Handle scheme-relative URLs (`//...` -> `https://...`), parse with `Url::parse`, verify scheme is `http`/`https`, verify host matches `REPLY_EMOTE_DOMAINS` suffixes, and verify path contains no unsafe characters or dot segments. Return `Some(format!("/proxy/pic/{}{}", host, url.path()))`.

- [ ] **Step 4: Implement `render_reply_content` using `REPLY_EMOTE_RE`**
Iterate through regex matches, escaping non-matching segments with `py_html_escape`. If matched token `[token]` exists in `emote_dict`, extract `url` and `meta.size` to generate `<img src="..." alt="..." ...>`, otherwise escape `[token]`.

- [ ] **Step 5: Run tests and verify**
Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_text_rs.py tests/test_reply_emoji.py`
Expected: PASS (all 22 tests pass).

- [ ] **Step 6: Commit**
```bash
git add crates/miku_core_rs/src/text.rs
git commit -m "refactor(core): use url and regex crates for emote rendering and search highlight stripping"
```

---

### Task 3: Refactor Live Danmaku Binary Unpacking with `byteorder` in `crates/miku_core_rs/src/live.rs`

**Files:**
- Modify: `crates/miku_core_rs/src/live.rs`
- Test: `tests/test_live_rs.py`

**Interfaces:**
- Consumes: `byteorder::{BigEndian, ReadBytesExt}`, `std::io::Cursor`.
- Produces: `unpack_live_danmaku<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyList>>`.

- [ ] **Step 1: Implement header reader with `byteorder::ReadBytesExt`**
Use `Cursor::new(data)` to read `packet_length: u32`, `header_length: u16`, `protover: u16`, `datapack_type: u32`, `seq: u32` via `ReadBytesExt::read_u32::<BigEndian>()` and `read_u16::<BigEndian>()`.

- [ ] **Step 2: Implement inner packet iteration with `byteorder`**
When reading decompressed Brotli payload, wrap buffer in `Cursor` or slice, read inner `packet_length`, `header_length`, `protover`, and `datapack_type`, and extract JSON or heartbeat payload.

- [ ] **Step 3: Run tests and verify**
Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_live_rs.py`
Expected: PASS (all 5 tests pass).

- [ ] **Step 4: Commit**
```bash
git add crates/miku_core_rs/src/live.rs
git commit -m "refactor(core): use byteorder crate for live danmaku binary packet unpacking"
```

---

### Task 4: Refactor DASH MPD Generation with `dash-mpd` in `crates/miku_core_rs/src/mpd.rs`

**Files:**
- Modify: `crates/miku_core_rs/src/mpd.rs`
- Test: `tests/test_mpd_rs.py`

**Interfaces:**
- Consumes: `dash_mpd::{AdaptationSet, BaseURL, Initialization, MPD, Period, Representation, SegmentBase}`.
- Produces: `generate_vod_mpd(bvid: &str, p: i64, data_dict: &Bound<'_, PyDict>) -> PyResult<String>`.

- [ ] **Step 1: Build strongly-typed `dash_mpd::MPD` data structures**
Extract video and audio items from `data_dict`. For each video/audio stream:
- Create `dash_mpd::Initialization { range: Some(init_range), ..Default::default() }`.
- Create `dash_mpd::SegmentBase { indexRange: Some(index_range), initialization: Some(init), ..Default::default() }`.
- Create `dash_mpd::BaseURL { base: format!("/proxy/dash/{bvid}/{p}/video/{qn}/{codecid}"), ..Default::default() }`.
- Create `dash_mpd::Representation { id: Some(qn.to_string()), bandwidth: Some(bw), width: Some(w), height: Some(h), codecs: Some(codecs), frameRate: Some(fps), sar: Some("1:1".into()), BaseURL: vec![base_url], SegmentBase: Some(seg_base), ..Default::default() }`.
- Group representations into video and audio `dash_mpd::AdaptationSet`s (`contentType: Some("video")`, `mimeType: Some("video/mp4")`, `subsegmentAlignment: Some(true)`, `par: Some("16:9")`).
- Put adaptation sets into `dash_mpd::Period { duration: Some(Duration::from_secs_f64(duration)), adaptations: vec![video_adapt, audio_adapt], ..Default::default() }`.
- Assemble `dash_mpd::MPD`:
  ```rust
  let mpd = MPD {
      xmlns: Some("urn:mpeg:dash:schema:mpd:2011".into()),
      profiles: Some("urn:mpeg:dash:profile:isoff-on-demand:2011".into()),
      mpdtype: Some("static".into()),
      mediaPresentationDuration: Some(Duration::from_secs_f64(duration)),
      minBufferTime: Some(Duration::from_secs_f64(min_buffer)),
      periods: vec![period],
      ..Default::default()
  };
  ```

- [ ] **Step 2: Serialize via `mpd.to_string()`**
Call `mpd.to_string()` to generate the standard DASH XML manifest.

- [ ] **Step 3: Run tests and verify manifest output**
Run: `npm run build:rust && PYTHONPATH=python uv run python -m unittest tests/test_mpd_rs.py`
Expected: PASS (all 3 tests pass, XML parses and verifies with minidom).

- [ ] **Step 4: Commit**
```bash
git add crates/miku_core_rs/src/mpd.rs
git commit -m "refactor(core): use dash-mpd crate for typed mpd manifest generation"
```

---

### Task 5: Full Suite Verification & Performance Benchmark

**Files:**
- Test: full project test suite, linter, benchmark script

- [ ] **Step 1: Run complete test suite**
Run: `npm test`
Expected: 111/111 PASS.

- [ ] **Step 2: Run linter and formatting**
Run: `npm run lint`
Expected: all checks pass (Ruff, Prettier, Clippy with -D warnings).

- [ ] **Step 3: Run performance benchmarks**
Run: `npm run benchmark`
Expected: speedups preserved across all operations.

- [ ] **Step 4: Commit and finalize**
```bash
git status
```
