#!/usr/bin/env python3
"""Benchmark Native Rust miku_core_rs vs Pure-Python Implementations."""

import json
import statistics
import struct
import sys
import time
import urllib.parse
from xml.dom import minidom

import brotli

# Ensure python/ is on sys.path
sys.path.insert(0, "python")

from api.client import _mixin_from_keys
from api.live import LiveDanmaku
from api.video import _av2bv_py, _bv2av_py, av2bv, bv2av
from danmaku import danmaku_xml_conv, danmaku_xml_fallback, parse_danmaku
from dash_mpd import _generate_vod_mpd_py, generate_vod_mpd
from transformers import _render_reply_content_py, render_reply_content, strip_search_highlight

try:
    import miku_core_rs
except ImportError:
    miku_core_rs = None

# This script compares both implementations; incomplete native builds cannot
# produce a complete comparison, even when application fallbacks are available.
_REQUIRED_NATIVE_EXPORTS = (
    "parse_danmaku_xml",
    "sign_wbi_params",
    "bv2av",
    "av2bv",
    "generate_vod_mpd",
    "render_reply_content",
    "unpack_live_danmaku",
)
if miku_core_rs is None:
    raise SystemExit("Rust comparison requires miku_core_rs. Build it with npm run build:rust.")
_missing_exports = [name for name in _REQUIRED_NATIVE_EXPORTS if not callable(getattr(miku_core_rs, name, None))]
if _missing_exports:
    raise SystemExit(f"Rust comparison requires native exports: {', '.join(_missing_exports)}. Rebuild with npm run build:rust.")


def generate_danmaku_xml(count: int = 5000) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<i>", "    <chatserver>chat.bilibili.com</chatserver>"]
    modes = [1, 4, 5, 6, 7]
    colors = [16777215, 0, 16711680, 65280, 255, 16776960]
    for i in range(count):
        t = round(i * 0.05, 3)
        m = modes[i % len(modes)]
        c = colors[i % len(colors)]
        lines.append(
            f'    <d p="{t},{m},25,{c},1600000000,0,user{i % 100},123456">Danmaku message #{i} 初音ミク &lt;3</d>'
        )
    lines.append("</i>")
    return "\n".join(lines)


def benchmark_danmaku(xml_payload: str, iterations: int = 30):
    print(f"\n--- 1. Danmaku XML Parsing ({len(xml_payload):,} bytes, 5,000 items, {iterations} iters) ---")
    if miku_core_rs and hasattr(miku_core_rs, "parse_danmaku_xml"):
        miku_core_rs.parse_danmaku_xml(xml_payload)
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            items_rs = miku_core_rs.parse_danmaku_xml(xml_payload)
            times_rs.append((time.perf_counter() - t0) * 1000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust quick-xml] avg={avg_rs:.2f}ms | parsed={len(items_rs):,} items ({len(items_rs) / (avg_rs / 1000.0):,.0f} items/sec)")
    else:
        avg_rs = None

    times_re = []
    danmaku_xml_fallback(xml_payload)
    for _ in range(iterations):
        t0 = time.perf_counter()
        items_re = danmaku_xml_fallback(xml_payload)
        times_re.append((time.perf_counter() - t0) * 1000.0)
    avg_re = statistics.mean(times_re)
    print(f"[Python Regex]   avg={avg_re:.2f}ms | parsed={len(items_re):,} items ({len(items_re) / (avg_re / 1000.0):,.0f} items/sec)")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_re / avg_rs:.1f}x faster")


def benchmark_wbi(iterations: int = 5000):
    print(f"\n--- 2. WBI Parameter Signing ({iterations} iters) ---")
    img_key = "7cd084481368485c8e428b3d1f3db9ed"
    sub_key = "411145e16d424f39af363d595240d7e3"
    mixin = _mixin_from_keys(img_key, sub_key)
    params = {
        "bvid": "BV1xx411c7xx",
        "cid": 12345678,
        "keyword": "初音ミク live concert 2026",
        "page": 1,
        "order": "pubdate",
        "from_source": "mikuinvidious",
    }

    if miku_core_rs and hasattr(miku_core_rs, "sign_wbi_params"):
        times_rs = []
        for _ in range(iterations):
            p = dict(params)
            t0 = time.perf_counter()
            _ = miku_core_rs.sign_wbi_params(p, mixin, wts=1700000000)
            times_rs.append((time.perf_counter() - t0) * 1_000_000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust WBI]   avg={avg_rs:.2f}µs | ops/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None

    import hashlib
    times_py = []
    for _ in range(iterations):
        p = dict(params)
        t0 = time.perf_counter()
        p.pop("w_rid", None)
        p["wts"] = 1700000000
        p["web_location"] = 1315873
        query = "&".join(
            f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(p.items())
        )
        p["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        times_py.append((time.perf_counter() - t0) * 1_000_000.0)
    avg_py = statistics.mean(times_py)
    print(f"[Python WBI] avg={avg_py:.2f}µs | ops/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_py / avg_rs:.1f}x faster")


def benchmark_bvid(iterations: int = 10000):
    print(f"\n--- 3. BV/AV ID Conversion ({iterations} iters) ---")
    sample_bvid = "BV17x411w7KC"
    sample_aid = 170001

    if miku_core_rs and hasattr(miku_core_rs, "bv2av"):
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = miku_core_rs.bv2av(sample_bvid)
            _ = miku_core_rs.av2bv(sample_aid)
            times_rs.append((time.perf_counter() - t0) * 1_000_000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust BV/AV]   avg={avg_rs:.2f}µs/pair | pairs/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None

    times_py = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = _bv2av_py(sample_bvid)
        _ = _av2bv_py(sample_aid)
        times_py.append((time.perf_counter() - t0) * 1_000_000.0)
    avg_py = statistics.mean(times_py)
    print(f"[Python BV/AV] avg={avg_py:.2f}µs/pair | pairs/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_py / avg_rs:.1f}x faster")


def benchmark_mpd(iterations: int = 2000):
    print(f"\n--- 4. DASH MPD Manifest Generation ({iterations} iters) ---")
    sample_dash = {
        "dash": {
            "duration": 360,
            "minBufferTime": 1.5,
            "video": [
                {
                    "id": qn,
                    "codecid": cid,
                    "bandwidth": 1000000 * (qn // 30),
                    "width": 1920,
                    "height": 1080,
                    "frameRate": "30",
                    "codecs": "avc1.640028" if cid == 7 else "hev1.1.6.L150.90",
                    "SegmentBase": {"Initialization": "0-999", "indexRange": "1000-2500"},
                }
                for qn in [32, 64, 80]
                for cid in [7, 12]
            ],
            "audio": [
                {
                    "id": 30280,
                    "codecid": 0,
                    "bandwidth": 320000,
                    "codecs": "mp4a.40.2",
                    "SegmentBase": {"Initialization": "0-800", "indexRange": "801-1200"},
                }
            ],
        }
    }

    if miku_core_rs and hasattr(miku_core_rs, "generate_vod_mpd"):
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = miku_core_rs.generate_vod_mpd("BV1xx411c7xx", 0, sample_dash)
            times_rs.append((time.perf_counter() - t0) * 1_000_000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust MPD]   avg={avg_rs:.2f}µs | ops/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None

    times_py = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = _generate_vod_mpd_py("BV1xx411c7xx", 0, sample_dash)
        times_py.append((time.perf_counter() - t0) * 1_000_000.0)
    avg_py = statistics.mean(times_py)
    print(f"[Python MPD] avg={avg_py:.2f}µs | ops/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_py / avg_rs:.1f}x faster")


def benchmark_text(iterations: int = 5000):
    print(f"\n--- 5. Comment Emote Rendering ({iterations} iters) ---")
    doge_url = "https://i0.hdslb.com/bfs/emote/doge.png"
    emote_dict = {
        "[doge]": {"url": doge_url, "meta": {"size": 1}},
        "[heart]": {"url": doge_url, "meta": {"size": 1}},
        "[flower]": {"url": doge_url, "meta": {"size": 2}},
    }
    msg = "Hello [doge] and [heart]! Check this [flower] and <script>alert('xss')</script> [unknown]"

    if miku_core_rs and hasattr(miku_core_rs, "render_reply_content"):
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = miku_core_rs.render_reply_content(msg, emote_dict)
            times_rs.append((time.perf_counter() - t0) * 1_000_000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust Emote]   avg={avg_rs:.2f}µs | ops/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None

    times_py = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = _render_reply_content_py(msg, emote_dict)
        times_py.append((time.perf_counter() - t0) * 1_000_000.0)
    avg_py = statistics.mean(times_py)
    print(f"[Python Emote] avg={avg_py:.2f}µs | ops/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_py / avg_rs:.1f}x faster")


def benchmark_live(iterations: int = 2000):
    print(f"\n--- 6. Live Danmaku Brotli Unpack ({iterations} iters) ---")
    packets = []
    for i in range(10):
        notice = json.dumps({"cmd": "DANMU_MSG", "info": [f"Message #{i} 初音ミク"]}).encode("utf-8")
        pkt = struct.pack(">IHHII", 16 + len(notice), 16, 0, 5, 1) + notice
        packets.append(pkt)
    compressed = brotli.compress(b"".join(packets))
    outer = struct.pack(">IHHII", 16 + len(compressed), 16, 3, 5, 1) + compressed

    ld = LiveDanmaku(123456)

    if miku_core_rs and hasattr(miku_core_rs, "unpack_live_danmaku"):
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = miku_core_rs.unpack_live_danmaku(outer)
            times_rs.append((time.perf_counter() - t0) * 1_000_000.0)
        avg_rs = statistics.mean(times_rs)
        print(f"[Rust Live]   avg={avg_rs:.2f}µs | ops/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None

    times_py = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = ld._unpack_py(outer)
        times_py.append((time.perf_counter() - t0) * 1_000_000.0)
    avg_py = statistics.mean(times_py)
    print(f"[Python Live] avg={avg_py:.2f}µs | ops/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python): {avg_py / avg_rs:.1f}x faster")


if __name__ == "__main__":
    print("==========================================================")
    print("  MikuInvidious Rust Core vs Pure-Python Benchmarks")
    print("==========================================================")
    payload = generate_danmaku_xml(5000)
    benchmark_danmaku(payload, iterations=20)
    benchmark_wbi(iterations=3000)
    benchmark_bvid(iterations=10000)
    benchmark_mpd(iterations=2000)
    benchmark_text(iterations=5000)
    benchmark_live(iterations=2000)
    print("\nBenchmark run complete.")
