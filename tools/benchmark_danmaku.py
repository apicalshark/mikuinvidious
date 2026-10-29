#!/usr/bin/env python3
"""Benchmark Danmaku XML parsing and WBI signing: Python vs Native Rust."""

import hashlib
import statistics
import sys
import time
import urllib.parse
from xml.dom import minidom

# Ensure python/ is on sys.path
sys.path.insert(0, "python")

from api.client import _mixin_from_keys
from danmaku import danmaku_xml_conv, danmaku_xml_fallback

try:
    import miku_core_rs
except ImportError:
    miku_core_rs = None


def generate_danmaku_xml(count: int = 5000) -> str:
    """Generate realistic Danmaku XML string with varied modes and styles."""
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


def benchmark_danmaku(xml_payload: str, iterations: int = 50):
    print(f"\n--- Danmaku Parsing Benchmark ({len(xml_payload):,} bytes, 5,000 items, {iterations} iterations) ---")

    # 1. Native Rust miku_core_rs
    if miku_core_rs and hasattr(miku_core_rs, "parse_danmaku_xml"):
        # Warmup
        miku_core_rs.parse_danmaku_xml(xml_payload)
        times_rs = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            items_rs = miku_core_rs.parse_danmaku_xml(xml_payload)
            t1 = time.perf_counter()
            times_rs.append((t1 - t0) * 1000.0)

        avg_rs = statistics.mean(times_rs)
        p50_rs = statistics.median(times_rs)
        p95_rs = statistics.quantiles(times_rs, n=100)[94] if len(times_rs) >= 100 else max(times_rs)
        print(
            f"[Rust quick-xml]   avg={avg_rs:.2f}ms | p50={p50_rs:.2f}ms | p95={p95_rs:.2f}ms | "
            f"parsed={len(items_rs):,} items ({len(items_rs) / (avg_rs / 1000.0):,.0f} items/sec)"
        )
    else:
        avg_rs = None
        print("[Rust quick-xml]   miku_core_rs NOT AVAILABLE")

    # 2. Pure Python Regex fallback
    times_re = []
    danmaku_xml_fallback(xml_payload)
    for _ in range(iterations):
        t0 = time.perf_counter()
        items_re = danmaku_xml_fallback(xml_payload)
        t1 = time.perf_counter()
        times_re.append((t1 - t0) * 1000.0)

    avg_re = statistics.mean(times_re)
    p50_re = statistics.median(times_re)
    p95_re = statistics.quantiles(times_re, n=100)[94] if len(times_re) >= 100 else max(times_re)
    print(
        f"[Python Regex]     avg={avg_re:.2f}ms | p50={p50_re:.2f}ms | p95={p95_re:.2f}ms | "
        f"parsed={len(items_re):,} items ({len(items_re) / (avg_re / 1000.0):,.0f} items/sec)"
    )

    # 3. Python minidom
    times_dom = []
    dom = minidom.parseString(xml_payload)
    danmaku_xml_conv(dom)
    # Fewer iterations for minidom due to high latency
    dom_iters = min(iterations, 10)
    for _ in range(dom_iters):
        t0 = time.perf_counter()
        d = minidom.parseString(xml_payload)
        items_dom = danmaku_xml_conv(d)
        t1 = time.perf_counter()
        times_dom.append((t1 - t0) * 1000.0)

    avg_dom = statistics.mean(times_dom)
    p50_dom = statistics.median(times_dom)
    p95_dom = max(times_dom)
    print(
        f"[Python minidom]   avg={avg_dom:.2f}ms | p50={p50_dom:.2f}ms | p95={p95_dom:.2f}ms | "
        f"parsed={len(items_dom):,} items ({len(items_dom) / (avg_dom / 1000.0):,.0f} items/sec)"
    )

    if avg_rs:
        print(f"\n>> Speedup (Rust vs Python minidom): {avg_dom / avg_rs:.1f}x faster")
        print(f">> Speedup (Rust vs Python Regex):   {avg_re / avg_rs:.1f}x faster")


def benchmark_wbi(iterations: int = 5000):
    print(f"\n--- WBI Parameter Signing Benchmark ({iterations} iterations) ---")
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
            t1 = time.perf_counter()
            times_rs.append((t1 - t0) * 1_000_000.0)  # microseconds

        avg_rs = statistics.mean(times_rs)
        p50_rs = statistics.median(times_rs)
        print(f"[Rust WBI]   avg={avg_rs:.2f}µs | p50={p50_rs:.2f}µs | ops/sec={1_000_000.0 / avg_rs:,.0f}")
    else:
        avg_rs = None
        print("[Rust WBI]   miku_core_rs NOT AVAILABLE")

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
        t1 = time.perf_counter()
        times_py.append((t1 - t0) * 1_000_000.0)

    avg_py = statistics.mean(times_py)
    p50_py = statistics.median(times_py)
    print(f"[Python WBI] avg={avg_py:.2f}µs | p50={p50_py:.2f}µs | ops/sec={1_000_000.0 / avg_py:,.0f}")

    if avg_rs:
        print(f">> Speedup (Rust vs Python WBI):     {avg_py / avg_rs:.1f}x faster")


if __name__ == "__main__":
    print("Generating 5,000 danmaku entries...")
    payload = generate_danmaku_xml(5000)
    benchmark_danmaku(payload, iterations=30)
    benchmark_wbi(iterations=5000)
