#!/usr/bin/env python3
"""Benchmark Jinja2 template rendering latency for cache misses."""

import asyncio
import statistics
import time

from app import app
from shared import render_template_with_theme


async def benchmark_template(name: str, template_fp: str, context: dict, iterations: int = 500):
    async with app.test_request_context("/"):
        # Warm-up compile
        await render_template_with_theme(template_fp, **context)

        timings_ms = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            rendered = await render_template_with_theme(template_fp, **context)
            t1 = time.perf_counter()
            timings_ms.append((t1 - t0) * 1000.0)

        p50 = statistics.median(timings_ms)
        p95 = statistics.quantiles(timings_ms, n=100)[94] if len(timings_ms) >= 100 else max(timings_ms)
        p99 = statistics.quantiles(timings_ms, n=100)[98] if len(timings_ms) >= 100 else max(timings_ms)
        avg = statistics.mean(timings_ms)

        print(
            f"[{name}] {iterations} iterations: "
            f"avg={avg:.3f}ms | p50={p50:.3f}ms | p95={p95:.3f}ms | p99={p99:.3f}ms | "
            f"rendered_len={len(rendered):,} chars"
        )
        return {"avg": avg, "p50": p50, "p95": p95, "p99": p99}


async def main():
    print("=== MikuInvidious Jinja2 Template Rendering Benchmark ===")

    # 1. Error page
    await benchmark_template(
        "error.html",
        "error.html",
        {"status": "404 Not Found", "desc": "Page not found", "suggest": "Go back home", "title": "Error"},
    )

    # 2. Convert page
    await benchmark_template(
        "convert.html",
        "convert.html",
        {"title": "B23 Shortlink Converter"},
    )

    # 3. Licenses page
    await benchmark_template(
        "licenses.html",
        "licenses.html",
        {"title": "Open Source Licenses"},
    )

    # 4. Home feed with mock video items
    mock_videos = [
        {
            "bvid": f"BV1xx411c7x{i}",
            "title": f"Test Video Title {i} with some descriptive text",
            "pic": "https://i0.hdslb.com/bfs/archive/test.jpg",
            "author": f"Author {i}",
            "author_id": 1000 + i,
            "views": 12345 * (i + 1),
            "danmaku": 543 * (i + 1),
            "duration": "03:45",
            "published": 1700000000 + i * 3600,
        }
        for i in range(16)
    ]
    await benchmark_template(
        "home.html (16 cards)",
        "home.html",
        {
            "title": "Home",
            "videos": mock_videos,
            "page": 1,
            "has_next": True,
            "is_home": True,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
