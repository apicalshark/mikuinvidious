# Rust Bindings & Anti-Spaghetti Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate LLM-generated runtime monkey patching, retire prototype pollution and frontend spaghetti, and accelerate synchronous CPU bottlenecks (Danmaku XML parsing and WBI signature generation) using safe PyO3 Rust Python bindings.

**Architecture:**
1. **Frontend Modernization**: Strip prototype pollution from `_helpers.js` and decompose the 1,883-line `player.js` monolith into clean, single-responsibility modules (`VodStreamManager`, `LiveStreamManager`, `DashPlayerManager`, `DanmakuOverlay`), encapsulating the Firefox 0-seek MSE workaround and removing polluting `window.*` globals.
2. **Backend Facade Retirement**: Eliminate dynamic `sys.modules[__name__]` proxies (`_ViewsFacade`, `_DashProxyFacade`) in `python/views.py` and `python/dash_proxy.py`, transitioning test monkeypatching directly to submodule boundaries (`views_video`, `dash_stream`, etc.).
3. **Safe Rust Core (`miku_core_rs`)**: Scaffold an in-tree Rust extension module with PyO3 0.30+ and Maturin 1.15+, implementing zero-copy streaming XML Danmaku parsing (via `quick-xml` with GIL detachment via `py.detach`) and optimized WBI parameter sorting and MD5 hashing, with graceful pure-Python fallback.

**Tech Stack:** Python 3.15+, Quart, Rust 1.85+ (current 1.99), PyO3 0.30.1, Maturin 1.15.0, `quick-xml`, Prettier, Ruff, unittest.

**Spec:** `doc/plan/issue-72-evaluation-and-option-a.md` and codebase audit findings.

## Global Constraints

- Verification MUST use `npm test`, `npm run lint`, and `npm run check:compile`. Never invoke ruff or djlint directly.
- The raw TCP socket streaming (`stream.py:CdnConnection`, `dash_stream.py`, `proxy.py`) and Granian reverse proxy architecture MUST remain intact.
- Rust extensions MUST support optional compilation / graceful pure-Python fallback so environments without `cargo` still run tests without crashing.
- Python code MUST target Python 3.15+; Rust code MUST target the 2024 edition.
- No global prototype modification (`Array.prototype.*`, `String.prototype.*`) is allowed in frontend JavaScript.

## Review Focus

1. **Danmaku Truncated/Malformed Payloads**: Bilibili upstream frequently serves malformed XML. The Rust parser must cleanly handle entity unescaping, unexpected EOF, and strip stray HTML tags without panicking or leaking memory.
2. **Dynamic Test Mocking Regressions**: Removing `_ViewsFacade` and `_DashProxyFacade` must not break existing test mocking across all 83 test suites in `tests/`.
3. **Firefox Zero-Seek Eviction**: The seek clamp workaround for Firefox MSE bug must remain intact without mutating `HTMLMediaElement.prototype` globally across all audio/video tags.
4. **PyO3 GIL Detachment**: Danmaku parsing over large (2–5MB) XML files must execute inside `py.detach(|| ...)` to prevent blocking Quart's async event loop.
5. **WBI Signature Compatibility**: Rust WBI signing must match Bilibili's exact RFC 3986 percent-encoding and `OE` permutation table without differences from `python/api/client.py:_enc_wbi`.

---

### Task 1: Prune Prototype Pollution & Modernize JS Helpers

**Files:**
- Modify: `static/themes/modern/js/_helpers.js:1-55`
- Test: `npm run lint:frontend`

**Interfaces:**
- Consumes: Native browser ES2020+ APIs
- Produces: `window.helpers` utility functions without altering native object prototypes

- [ ] **Step 1: Inspect and eliminate prototype pollution in `_helpers.js`**

Remove manual assignments to `Array.prototype.find`, `NodeList.prototype.forEach`, `String.prototype.includes`, and `String.prototype.startsWith`. Replace internal usages with native standard methods or explicit `helpers.*` methods.

- [ ] **Step 2: Clean up mockHTMLDetailsElement legacy workaround**

Ensure `details` element detection does not leave unnecessary global flags.

- [ ] **Step 3: Run frontend linter and format**

Run: `npm run lint:frontend`
Expected: PASS with 0 linting errors.

- [ ] **Step 4: Commit**

```bash
git add static/themes/modern/js/_helpers.js
git commit -m "refactor(js): remove prototype pollution from helpers"
```

---

### Task 2: Modularize Frontend Player Architecture

**Files:**
- Modify: `static/themes/modern/js/player.js`
- Create: `static/themes/modern/js/player_danmaku.js` (or clean module separation)
- Test: `npm run lint:frontend`

**Interfaces:**
- Consumes: `mpegts.js`, `dash.js`, `danmaku.js`, `hls.js`
- Produces: `window.initMikuPlayer(options)` and scoped manager instances without polluting top-level `window.*` globals

- [ ] **Step 1: Isolate Firefox Zero-Seek Clamp**

Refactor the `currentTime` clamp in `player.js` so it only attaches directly to the specific `#player` video instance via `Object.defineProperty(video, "currentTime", ...)` rather than querying and hooking `HTMLMediaElement.prototype`.

- [ ] **Step 2: Encapsulate global state into a single MikuPlayerContext**

Replace loose `window.vodManager`, `window.dashManager`, `window.vodBufferController`, `window.liveManager`, `window.flvPlayer`, `window.hls`, `window.dm`, `window.dm_status` with a unified `window.mikuPlayer` controller instance.

- [ ] **Step 3: Run frontend linter and verify template integration**

Run: `npm run lint:frontend`
Expected: PASS with clean formatting.

- [ ] **Step 4: Commit**

```bash
git add static/themes/modern/js/player.js
git commit -m "refactor(player): encapsulate player state and isolate seek clamp"
```

---

### Task 3: Eliminate Dynamic Module Proxy Facades

**Files:**
- Modify: `python/views.py`
- Modify: `python/dash_proxy.py`
- Modify: `python/views_home.py:165-175`
- Modify: `tests/test_review_regressions.py`
- Modify: `tests/test_b23_routes.py`
- Modify: `tests/test_convert_view.py`
- Test: `npm test`

**Interfaces:**
- Consumes: Real submodule symbols (`views_video`, `views_space`, `views_home`, `dash_stream`, etc.)
- Produces: Clean, static re-exports from `views.py` and `dash_proxy.py` without replacing `sys.modules[__name__]`

- [ ] **Step 1: Write test ensuring direct submodule imports work without facade**

Verify that `import views_home`, `import views_video`, and `import dash_stream` can be patched at their true module paths.

- [ ] **Step 2: Replace `_ViewsFacade` with explicit module re-exports in `python/views.py`**

Remove `class _ViewsFacade(types.ModuleType)` and `sys.modules[__name__] = _facade`. Explicitly import and expose blueprint routes and shared handlers so `views.py` remains a clean static facade.

- [ ] **Step 3: Replace `_DashProxyFacade` with static re-exports in `python/dash_proxy.py`**

Remove `class _DashProxyFacade(types.ModuleType)` and `sys.modules[__name__] = _facade`. Re-export symbols directly from `dash_common`, `dash_fetch`, `dash_mpd`, `dash_stream`, `dash_download_track`, and `dash_download_job`.

- [ ] **Step 4: Update test mock call-sites to patch canonical target modules**

Update tests in `tests/test_review_regressions.py`, `tests/test_convert_view.py`, and `tests/test_b23_routes.py` to patch symbols at their defined locations (e.g. `views_video.comment.get_comments` or `views_home.resolve_b23_target`).

- [ ] **Step 5: Run test suite and bytecode compilation check**

Run: `npm test`
Expected: 83/83 tests pass.
Run: `npm run check:compile`
Expected: Compiles with 0 syntax or import errors.

- [ ] **Step 6: Commit**

```bash
git add python/views.py python/dash_proxy.py python/views_home.py tests/
git commit -m "refactor(views): retire dynamic sys.modules monkeypatch facades"
```

---

### Task 4: Setup PyO3 0.30 & Maturin 1.15 Extension Package

**Files:**
- Create: `crates/miku_core_rs/Cargo.toml`
- Create: `crates/miku_core_rs/src/lib.rs`
- Modify: `pyproject.toml`
- Test: `uv run maturin develop --manifest-path crates/miku_core_rs/Cargo.toml`

**Interfaces:**
- Consumes: Rust 1.85+, Python 3.15
- Produces: `miku_core_rs` importable Python binary extension

- [ ] **Step 1: Configure Cargo crate for PyO3**

Create `crates/miku_core_rs/Cargo.toml` configuring:
- `crate-type = ["cdylib", "rlib"]`
- `pyo3 = { version = "0.30", features = ["abi3-py310", "extension-module"] }`
- `quick-xml = { version = "0.37", features = ["serialize"] }`
- `md-5 = "0.10"`

- [ ] **Step 2: Implement minimal `lib.rs` module entry point**

```rust
use pyo3::prelude::*;

#[pymodule]
mod miku_core_rs {
    use super::*;

    #[pyfunction]
    fn is_available() -> bool {
        true
    }
}
```

- [ ] **Step 3: Add `maturin` build support and test compilation**

Compile the extension and verify it can be imported via Python:
Run: `uv run python -c "import miku_core_rs; print(miku_core_rs.is_available())"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add crates/miku_core_rs/ pyproject.toml
git commit -m "feat(rust): scaffold miku_core_rs PyO3 extension crate"
```

---

### Task 5: Implement High-Performance Rust Danmaku XML Parser

**Files:**
- Create: `crates/miku_core_rs/src/danmaku.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/danmaku.py`
- Test: `tests/test_danmaku_rs.py`

**Interfaces:**
- Consumes: Raw Bilibili Danmaku XML `&str`
- Produces: `parse_danmaku_xml(xml: str) -> list[dict]` matching existing `danmaku_xml_conv` structure

- [ ] **Step 1: Write test for Danmaku parser with standard and malformed XML**

Verify that `parse_danmaku_xml` produces the exact same dictionary structure (`text`, `mode`, `time`, `style`) as Python's `_danmaku_entry`.

- [ ] **Step 2: Implement streaming `quick-xml` parser in `crates/miku_core_rs/src/danmaku.rs`**

Use `quick-xml::Reader::from_str` with `py.detach(|| ...)` to parse `<d p="...">text</d>` nodes without holding the Python GIL. Support unescaping XML entities and graceful recovery from truncated streams.

- [ ] **Step 3: Integrate into `python/danmaku.py` with fallback**

Wrap the native call in `python/danmaku.py`:
```python
try:
    from miku_core_rs import parse_danmaku_xml
except ImportError:
    parse_danmaku_xml = None
```
Use `parse_danmaku_xml` as the primary fast path, falling back to Python `danmaku_xml_fallback` if the extension is absent.

- [ ] **Step 4: Run unit tests and verify output**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_danmaku_rs.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/miku_core_rs/ python/danmaku.py tests/test_danmaku_rs.py
git commit -m "perf(danmaku): implement zero-copy Rust Danmaku parser via PyO3"
```

---

### Task 6: Implement High-Performance Rust WBI Parameter Signer

**Files:**
- Create: `crates/miku_core_rs/src/wbi.rs`
- Modify: `crates/miku_core_rs/src/lib.rs`
- Modify: `python/api/client.py:345-367`
- Test: `tests/test_wbi_rs.py`

**Interfaces:**
- Consumes: `params: dict[str, Any]`, `mixin_key: str`
- Produces: `sign_wbi_params(params: dict, mixin_key: str) -> dict` returning updated params with `wts` and `w_rid`

- [ ] **Step 1: Write unit test validating Rust WBI signing against Python output**

Validate known test vectors (e.g. mixin keys, query string with spaces, integer and string parameters).

- [ ] **Step 2: Implement `sign_wbi_params` in `crates/miku_core_rs/src/wbi.rs`**

Implement `OE` table permutation and RFC 3986 percent-encoding with fast MD5 hashing in Rust.

- [ ] **Step 3: Wire into `python/api/client.py:_enc_wbi` with fallback**

Invoke `sign_wbi_params` when available; retain Python implementation when unavailable.

- [ ] **Step 4: Run unit tests**

Run: `PYTHONPATH=python uv run python -m unittest tests/test_wbi_rs.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/miku_core_rs/ python/api/client.py tests/test_wbi_rs.py
git commit -m "perf(wbi): implement native Rust WBI parameter signer"
```

---

### Task 7: Full Verification Suite & Performance Benchmarks

**Files:**
- Create: `tools/benchmark_danmaku.py`
- Test: `npm test`
- Test: `npm run lint`
- Test: `npm run check:compile`

**Interfaces:**
- Consumes: All updated Python, Rust, and JavaScript code
- Produces: Verified codebase and empirical benchmark metrics comparing pure Python vs Rust

- [ ] **Step 1: Implement benchmark script `tools/benchmark_danmaku.py`**

Benchmark parsing 5,000 danmaku items over 100 iterations comparing `xml.dom.minidom` vs `miku_core_rs.parse_danmaku_xml`.

- [ ] **Step 2: Run verification scripts**

Run: `npm test`
Expected: All 83+ tests pass.
Run: `npm run lint`
Expected: 0 lint errors across Python and frontend assets.
Run: `npm run check:compile`
Expected: 0 syntax or import errors.

- [ ] **Step 3: Commit**

```bash
git add tools/benchmark_danmaku.py
git commit -m "test(bench): add Danmaku Rust vs Python benchmark tool"
```
