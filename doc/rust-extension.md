# Rust Extension (`miku_core_rs`)

MikuInvidious includes an optional native Rust extension (`crates/miku_core_rs`) built with [PyO3](https://pyo3.rs/) and [Maturin](https://www.maturin.rs/) (using stable ABI `abi3-py310`).

The extension offloads high-volume, CPU-bound operations from Python to Rust, drastically improving response latency and concurrency under heavy traffic.

---

## 1. Accelerated Operations

### Zero-Copy Streaming Danmaku XML Parser
- **File:** `crates/miku_core_rs/src/danmaku.rs`
- **Engine:** [`quick-xml`](https://github.com/tafia/quick-xml)
- **Concurrency:** Uses `py.detach(|| ...)` to completely release the Python GIL (Global Interpreter Lock) while processing large XML documents. Asynchronous Quart worker loops can handle other network requests without blocking.
- **Robustness:** Gracefully handles XML entity unescaping (`&lt;`, `&gt;`, `&amp;`, `&quot;`, `&apos;`) and recovers parsed entries from truncated upstream XML payloads.
- **Benchmark:** Parses ~436,000 items/sec (**~4.4x faster** than `xml.dom.minidom`).

### RFC 3986 WBI Parameter Signer
- **File:** `crates/miku_core_rs/src/wbi.rs`
- **Engine:** Native MD5 hashing and strict RFC 3986 percent-encoding (ensuring `%20` whitespace encoding instead of `+`).
- **Benchmark:** ~370,000 sign operations/sec (**~3.4x faster** than pure-Python `urllib.parse.quote` + `hashlib.md5`).

---

## 2. Dual-Path Architecture & Fallback Guarantee

**Rust is 100% optional.**

Every native call is wrapped with an automatic fallback in Python:
- `python/danmaku.py:parse_danmaku`: If `miku_core_rs` is not installed or raises an unexpected error, it seamlessly falls back to Python regex extraction (`danmaku_xml_fallback`).
- `python/api/client.py:_enc_wbi` & `_mixin_from_keys`: If `miku_core_rs` is not installed, it falls back to the native Python signing pipeline.

The application, test suite, and web endpoints function identically whether the extension is compiled or not.

---

## 3. How to Build & Install

### A. Local / Native Development

1. **Prerequisite:** Install the Rust toolchain (Rust 1.85+):
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   source "$HOME/.cargo/env"
   ```

2. **Build and install into the local virtual environment:**
   ```bash
   npm run build:rust
   ```
   *(Or using uv directly: `uv run maturin develop --release --manifest-path crates/miku_core_rs/Cargo.toml`)*

3. **Verify the extension:**
   ```bash
   uv run python -c "import miku_core_rs; print('Rust extension available:', miku_core_rs.is_available())"
   ```

4. **Run performance benchmarks:**
   ```bash
   uv run python tools/benchmark_danmaku.py
   ```

---

### B. Docker Compose

Docker Compose automatically compiles the Rust extension using a **multi-stage build**:
1. Stage 1 (`rust-builder`): Compiles `crates/miku_core_rs` into a `.whl` package inside an Alpine builder.
2. Stage 2 (`runtime`): Installs the wheel into the Python virtual environment.

No Rust toolchain or build packages remain in the final runtime container image.

To build and start with Docker Compose:
```bash
docker compose up -d --build
```

To verify inside the running container:
```bash
docker compose exec app uv run --no-dev python -c "import miku_core_rs; print('Rust active in container:', miku_core_rs.is_available())"
```
