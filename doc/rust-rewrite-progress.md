# Rust Backend Rewrite & Coexistence Progress

This document tracks the ongoing incremental migration of MikuInvidious backend workloads from Python to Rust via the native [`crates/miku_core_rs`](../crates/miku_core_rs/) PyO3 extension, while maintaining seamless coexistence with the Python Quart application.

---

## 1. Architectural Strategy & Coexistence Invariants

Instead of partitioning the application across separate microservices or replacing the ASGI web framework upfront, MikuInvidious follows an **in-process incremental migration**:

- **Runtime & Host:** The web application runtime remains Python 3.15+ (Quart ASGI server running on Granian).
- **Native Extension:** Performance-critical modules, parsers, and compute-heavy algorithms are implemented in Rust within `crates/miku_core_rs` using [PyO3](https://pyo3.rs/) 0.30+ and Maturin, targeting the Python Stable ABI (`abi3-py315`).
- **100% Transparent Pure-Python Fallbacks:** Every native function has a matching pure-Python fallback. If `miku_core_rs` is not compiled or fails to load, the application, test suite, and web endpoints continue to function identically with zero downtime or breaking changes.
- **GIL Detachment:** Compute-heavy operations (decompression, large XML parsing, string formatting) release Python's Global Interpreter Lock (`py.allow_threads` / `py.detach`), allowing the Quart event loop to process concurrent network requests unimpeded.
- **Container Isolation:** Multi-stage Docker builds compile the native `.whl` in a `rust-builder` stage and install it into a clean Alpine runtime, keeping build tools out of production images.

---

## 2. Completed Milestones & Current Capabilities

| Module | Native Source | Python Host & Fallback | Engine / Crates | Status | Verification & Dual-Path Tests |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Danmaku XML Streaming Parser** | `crates/miku_core_rs/src/danmaku.rs` | `python/danmaku.py` (`parse_danmaku`, `danmaku_xml_conv`) | `quick-xml 0.42` (`py.detach`) | Completed | `tests/test_danmaku.py`, `tests/test_danmaku_rs.py` |
| **WBI Signer & Mixin Key Permutation** | `crates/miku_core_rs/src/wbi.rs` | `python/api/client.py` (`_enc_wbi`, `_mixin_from_keys`) | Strict RFC 3986 percent-encoding + `md5` | Completed | `tests/test_wbi_rs.py` |
| **BV/AV Identifier Conversion** | `crates/miku_core_rs/src/bvid.rs` | `python/api/video.py` (`bv2av`, `av2bv`) | Base58 permutation table + bitwise XOR/mask | Completed | `tests/test_bvid_rs.py` |
| **VOD DASH MPD Manifest Generation** | `crates/miku_core_rs/src/mpd.rs` | `python/dash_mpd.py` (`generate_vod_mpd`) | ISO/IEC 23009-1 MPD generator with codec grouping (`py.allow_threads`) | Completed | `tests/test_mpd_rs.py` |
| **Reply Emotes & Search Highlighting** | `crates/miku_core_rs/src/text.rs` | `python/transformers.py` (`render_reply_content`, `strip_search_highlight`) | Tokenizer, HTML escaping & domain whitelisting | Completed | `tests/test_text_rs.py`, `tests/test_reply_emoji.py` |
| **Live Danmaku Binary Protocol & Brotli** | `crates/miku_core_rs/src/live.rs` | `python/api/live.py` (`LiveDanmaku._unpack`) | `brotli 7.0`, `serde_json 1.0`, 16-byte header unpacker (`py.allow_threads`) | Completed | `tests/test_live_rs.py` |
| **Core Benchmarking Suite** | `tools/benchmark_core.py` | Python fallback runner | `time.perf_counter`, statistics | Completed | Validates native speedups across all 6 subsystems |

---

## 3. Current Phase: Evaluation of Next Targets

> **Current Status:** Still evaluating candidate workloads and module boundaries. No subsequent modules are locked in yet.

The next candidates are being evaluated based on performance profiling, maintenance complexity, and PyO3 boundary ergonomics.

### Candidate Areas Under Evaluation

1. **Media Streaming & CDN Proxying Hot Paths**
   - *Target areas:* Range request slicing, byte-stream pacing watchdogs, and multi-mirror failover state (`python/dash_stream.py`, `python/dash_download_track.py`).
   - *Considerations:* Requires careful integration with Quart / `asyncio` streaming response bodies and socket lifetimes.

2. **API HTTP Transport & Signing Pipeline**
   - *Target areas:* Upstream HTTP request construction, header assembly, and ticket verification (`python/api/client.py`).
   - *Considerations:* Must maintain exact compatibility with Cloudflare WARP SOCKS5 proxying and residential vs. datacenter risk-control invariants.

3. **Response Transformers & Feed Normalization**
   - *Target areas:* Large JSON response transformation from Bilibili upstream schemas into frontend card models (`python/transformers.py`).
   - *Considerations:* Assessing whether PyO3 dictionary conversion overhead outweighs pure-Python deserialization benefits for smaller payloads.

4. **Session & Redis Cache Key Serialization**
   - *Target areas:* High-frequency cache key generation, signature hashing, and expiration checks.
   - *Considerations:* Evaluating memory allocation benefits vs. implementation overhead.

### Evaluation Criteria

- **Compute vs. I/O Ratio:** Priority is given to CPU-heavy bottlenecks (serialization, parsing, compression, mathematical/bitwise operations).
- **PyO3 Boundary Overhead:** Workloads must minimize conversions between complex nested Python objects and Rust structs.
- **Fallback Parity:** Pure-Python fallback code must remain clean, thoroughly tested, and functionally indistinguishable.
- **Error Safety:** Rust code must handle malformed network inputs, corrupted streams, and unexpected types gracefully without panics.

---

## 4. Progress Checklist

- [x] Establish PyO3 extension crate (`crates/miku_core_rs`) with Maturin & Python 3.15+ Stable ABI (`abi3-py315`).
- [x] Implement multi-stage Docker build separating Rust compilation from final runtime image.
- [x] Implement automated parity test suite (`tests/test_*_rs.py`) validating identical behavior between Rust and Python fallbacks.
- [x] Port Danmaku XML stream parsing (`danmaku.rs`).
- [x] Port WBI parameter signing and mixin key derivation (`wbi.rs`).
- [x] Port BV/AV identifier conversion (`bvid.rs`).
- [x] Port VOD DASH MPD XML manifest generation (`mpd.rs`).
- [x] Port reply emote rendering and search keyword stripping (`text.rs`).
- [x] Port live danmaku binary packet unpacking and Brotli decompression (`live.rs`).
- [x] Consolidate comprehensive benchmarking suite (`tools/benchmark_core.py`).
- [ ] Complete evaluation of candidate modules for next migration phase.
- [ ] Select and plan implementation for next prioritized module.

---

## 5. Related Documentation

- [`doc/rust-extension.md`](./rust-extension.md): Build, installation, and setup instructions for `miku_core_rs`.
- [`doc/plan/2026-10-29-rust-core-acceleration.md`](./plan/2026-10-29-rust-core-acceleration.md): Historical implementation plan for core acceleration tasks.
- [`tools/benchmark_core.py`](../tools/benchmark_core.py): Benchmarking utility comparing native Rust vs. Python fallbacks.
