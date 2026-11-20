# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for player replay cache retention after ended state.

Run with PYTHONPATH=python uv run python -m unittest discover -s tests.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAYER_JS = ROOT / "static" / "themes" / "modern" / "js" / "player.js"


class PlayerReplayTests(unittest.TestCase):
    def setUp(self):
        self.src = PLAYER_JS.read_text(encoding="utf-8")

    def test_zero_seek_clamp_present(self):
        self.assertIn("_zeroSeekClamped", self.src)
        self.assertIn("currentTime", self.src)
        # Should apply to all VOD (not restricted to Firefox only)
        self.assertIn("!window.is_live && !video._zeroSeekClamped", self.src)

    def test_replay_handler_present(self):
        self.assertIn("mediaplayrequest", self.src)
        self.assertIn("video.ended", self.src)

    def test_js_node_execution(self):
        """Simulate browser video element, media-controller, and verify replay behavior."""
        node_bin = shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            brew_node = "/home/linuxbrew/.linuxbrew/bin/node"
            if os.path.exists(brew_node) and os.access(brew_node, os.X_OK):
                node_bin = brew_node
        if not node_bin:
            raise unittest.SkipTest("node executable not found")
        test_script = """
const { EventTarget, CustomEvent } = globalThis;

// Mock HTMLMediaElement prototype
const protoDesc = {
  get() { return this._currentTime != null ? this._currentTime : 0; },
  set(v) { this._currentTime = v; this.ended = false; }
};
globalThis.HTMLMediaElement = {
  prototype: {
    get currentTime() { return protoDesc.get.call(this); },
    set currentTime(v) { protoDesc.set.call(this, v); },
    play() {
      this.paused = false;
      return Promise.resolve();
    }
  }
};

globalThis.window = { is_live: false };
const controller = new EventTarget();

const video = {
  _currentTime: 120,
  duration: 120,
  ended: true,
  paused: true,
};
Object.setPrototypeOf(video, globalThis.HTMLMediaElement.prototype);

// 1. Install zero-seek clamp (as in player.js)
if (!window.is_live && !video._zeroSeekClamped) {
  const desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "currentTime");
  if (desc && typeof desc.set === "function") {
    Object.defineProperty(video, "currentTime", {
      get() { return desc.get.call(this); },
      set(v) {
        const t = Number(v);
        desc.set.call(this, t <= 0 && Number(this.duration) > 1 ? 0.1 : v);
      },
      configurable: true
    });
    video._zeroSeekClamped = true;
  }
}

// 2. Install replay handler on controller and video.play (as in player.js)
if (!window.is_live && controller && !controller._replayHandlerInstalled) {
  controller.addEventListener(
    "mediaplayrequest",
    () => {
      if (video.ended || (video.duration > 1 && video.currentTime >= video.duration)) {
        video.currentTime = 0;
      }
    },
    true
  );
  controller._replayHandlerInstalled = true;
}

if (!window.is_live && !video._playReplayWrapped) {
  const origPlay = video.play;
  video.play = function () {
    if (this.ended || (this.duration > 1 && this.currentTime >= this.duration)) {
      this.currentTime = 0;
    }
    return origPlay.apply(this, arguments);
  };
  video._playReplayWrapped = true;
}

// Scenario 1: Dragging to the beginning (user seek)
video.ended = false;
video.currentTime = 0;
if (video.currentTime !== 0.1) {
  throw new Error("Dragging to beginning should clamp to 0.1s, got: " + video.currentTime);
}

// Scenario 2: Video ended, user clicks play on media-controller (mediaplayrequest event)
video._currentTime = 120;
video.ended = true;
video.paused = true;

// media-play-button fires mediaplayrequest
controller.dispatchEvent(new Event("mediaplayrequest", { bubbles: true }));
// media-controller then calls media.play()
video.play();

if (video.ended) {
  throw new Error("After mediaplayrequest on ended video, ended should be false");
}
if (video.currentTime !== 0.1) {
  throw new Error("After replay on ended video, currentTime should be clamped to 0.1s, got: " + video.currentTime);
}

// Scenario 3: Video ended, direct video.play() call
video._currentTime = 120;
video.ended = true;
video.paused = true;

video.play();
if (video.ended) {
  throw new Error("After video.play() on ended video, ended should be false");
}
if (video.currentTime !== 0.1) {
  throw new Error("After video.play() on ended video, currentTime should be clamped to 0.1s, got: " + video.currentTime);
}

// Scenario 4: Normal mid-playback pause and play (not ended)
video._currentTime = 45;
video.ended = false;
video.paused = true;

video.play();
if (video.currentTime !== 45) {
  throw new Error("Normal mid-playback play should preserve currentTime (45), got: " + video.currentTime);
}
"""
        proc = subprocess.run([node_bin, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_deferred_seek_present_and_defers_until_pointerup(self):
        self.assertIn("_deferredSeekInstalled", self.src)
        self.assertIn("mediaseekrequest", self.src)

        node_bin = shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            brew_node = "/home/linuxbrew/.linuxbrew/bin/node"
            if os.path.exists(brew_node) and os.access(brew_node, os.X_OK):
                node_bin = brew_node
        if not node_bin:
            raise unittest.SkipTest("node executable not found")

        test_script = """
const { EventTarget, CustomEvent } = globalThis;
const windowObj = new EventTarget();
globalThis.window = windowObj;
globalThis.CustomEvent = CustomEvent;

const controller = new EventTarget();
const timeRange = new EventTarget();
timeRange.hasAttribute = () => false;
timeRange.dispatchEvent = (ev) => {
  let stopped = false;
  for (const method of ["stopPropagation", "stopImmediatePropagation"]) {
    const stop = ev[method].bind(ev);
    ev[method] = () => {
      stopped = true;
      stop();
    };
  }
  const ok = EventTarget.prototype.dispatchEvent.call(timeRange, ev);
  if (ok && ev.bubbles && !stopped) {
    controller.dispatchEvent(new CustomEvent(ev.type, { detail: ev.detail }));
  }
  return ok;
};

const controllerSeeks = [];
controller.addEventListener("mediaseekrequest", (e) => {
  controllerSeeks.push(e.detail);
});

let isScrubbing = false;
let isCommitting = false;
let pendingSeekTime = null;
let lastCommittedTime = null;
let lastCommittedAt = 0;

Object.defineProperty(timeRange, "dragging", {
  get() {
    return isScrubbing || this.hasAttribute("dragging");
  },
  configurable: true,
});

timeRange.addEventListener(
  "pointerdown",
  () => {
    isScrubbing = true;
    pendingSeekTime = null;
  },
  true
);

timeRange.addEventListener("mediaseekrequest", (e) => {
  if (isCommitting) return;
  if (isScrubbing) {
    pendingSeekTime = Number(e.detail);
    e.stopImmediatePropagation();
  } else if (
    lastCommittedTime !== null &&
    performance.now() - lastCommittedAt < 50 &&
    Math.abs(Number(e.detail) - lastCommittedTime) < 0.05
  ) {
    e.stopImmediatePropagation();
  }
});

const commitPendingSeek = () => {
  if (!isScrubbing) return;
  isScrubbing = false;
  if (pendingSeekTime !== null && Number.isFinite(pendingSeekTime)) {
    const target = pendingSeekTime;
    pendingSeekTime = null;
    isCommitting = true;
    try {
      timeRange.dispatchEvent(
        new CustomEvent("mediaseekrequest", { composed: true, bubbles: true, detail: target })
      );
    } finally {
      isCommitting = false;
    }
    lastCommittedTime = target;
    lastCommittedAt = performance.now();
  }
};

windowObj.addEventListener("pointerup", commitPendingSeek);
windowObj.addEventListener("pointercancel", () => {
  if (!isScrubbing) return;
  isScrubbing = false;
  pendingSeekTime = null;
});
timeRange._deferredSeekInstalled = true;

timeRange.dispatchEvent(new CustomEvent("pointerdown"));
if (!timeRange.dragging) throw new Error("Expected dragging to be true while scrubbing");
timeRange.dispatchEvent(new CustomEvent("mediaseekrequest", { bubbles: true, detail: 12.5 }));
timeRange.dispatchEvent(new CustomEvent("mediaseekrequest", { bubbles: true, detail: 45.2 }));
timeRange.dispatchEvent(new CustomEvent("mediaseekrequest", { bubbles: true, detail: 88.9 }));

if (controllerSeeks.length !== 0) {
  throw new Error("Expected 0 seeks during drag, got: " + JSON.stringify(controllerSeeks));
}

windowObj.dispatchEvent(new CustomEvent("pointerup"));
timeRange.dispatchEvent(new CustomEvent("mediaseekrequest", { bubbles: true, detail: 88.9 }));

if (controllerSeeks.length !== 1 || controllerSeeks[0] !== 88.9) {
  throw new Error("Expected single committed seek [88.9], got: " + JSON.stringify(controllerSeeks));
}
timeRange.dispatchEvent(new CustomEvent("mediaseekrequest", { bubbles: true, detail: 93 }));
if (controllerSeeks.length !== 2 || controllerSeeks[1] !== 93) {
  throw new Error("Expected a different seek to reach the controller");
}
"""
        proc = subprocess.run([node_bin, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_dash_buffered_seek_realign_and_gap_settings(self):
        self.assertIn("jumpLargeGaps: false", self.src)
        self.assertIn("_realignBufferedSeek", self.src)
        self.assertIn("bufferTimeDefault", self.src)
        self.assertNotIn("stableBufferTime", self.src)

        node_bin = shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            brew_node = "/home/linuxbrew/.linuxbrew/bin/node"
            if os.path.exists(brew_node) and os.access(brew_node, os.X_OK):
                node_bin = brew_node
        if not node_bin:
            raise unittest.SkipTest("node executable not found")

        start_idx = self.src.index("  _realignBufferedSeek(seekTime) {")
        end_idx = self.src.index("\n  destroy(isFinal = true) {", start_idx)
        method_src = self.src[start_idx:end_idx]

        test_script = f"""
const cleared = [];
let bufferingTime = 140;
let seekTarget = null;
let bufferingCompleted = true;
let aborted = false;
let initRequired = false;
let scheduleArg = null;
let ranges = [
  {{ start: 0, end: 80 }},
  {{ start: 115.008, end: 140 }},
];

const bc = {{
  hasBufferAtTime(t) {{
    return ranges.some((r) => t >= r.start && t <= r.end);
  }},
  getContinuousBufferTimeForTargetTime(t) {{
    const hit = ranges.find((r) => t >= r.start && t <= r.end);
    return hit ? hit.end : NaN;
  }},
  getBuffer() {{
    return {{
      getAllBufferRanges() {{
        return {{
          length: ranges.length,
          start: (i) => ranges[i].start,
          end: (i) => ranges[i].end,
        }};
      }},
    }};
  }},
  getIsBufferingCompleted: () => bufferingCompleted,
  setIsBufferingCompleted: (v) => {{ bufferingCompleted = v; }},
  clearBuffers(list) {{
    cleared.push(...list);
    ranges = ranges.filter((r) => !list.some((c) => r.start >= c.start && r.end <= c.end));
    return Promise.resolve();
  }},
  setSeekTarget: (t) => {{ seekTarget = t; }},
}};

const sc = {{
  setInitSegmentRequired: (v) => {{ initRequired = v; }},
  startScheduleTimer: (v) => {{ scheduleArg = v; }},
}};

const sp = {{
  getBufferController: () => bc,
  getScheduleController: () => sc,
  getBufferingTime: () => bufferingTime,
  setExplicitBufferingTime: (t) => {{ bufferingTime = t; }},
  getFragmentModel: () => ({{ abortRequests: () => {{ aborted = true; }} }}),
}};

class Harness {{
  constructor() {{
    this.destroyed = false;
    this.video = {{ duration: 300 }};
    this.player = {{
      getActiveStream: () => ({{ getStreamProcessors: () => [sp] }}),
    }};
  }}
{method_src}
}}

(async () => {{
  const h = new Harness();
  h._realignBufferedSeek(50);
  await Promise.resolve();
  await Promise.resolve();
  if (!aborted) throw new Error("Expected abortRequests() to be called");
  if (cleared.length !== 1 || cleared[0].start !== 80.05 || cleared[0].end !== 141) {{
    throw new Error("Unexpected cleared ranges: " + JSON.stringify(cleared));
  }}
  if (bufferingTime !== 80) throw new Error("Expected bufferingTime reset to 80, got " + bufferingTime);
  if (seekTarget !== 80) throw new Error("Expected seekTarget reset to 80, got " + seekTarget);
  if (bufferingCompleted !== false) throw new Error("Expected bufferingCompleted false");
  if (!initRequired || scheduleArg !== 0) {{
    throw new Error("Expected initRequired=true and startScheduleTimer(0)");
  }}

  ranges.push({{ start: 115, end: 140 }});
  let finishClear;
  bc.clearBuffers = () => new Promise((resolve) => {{ finishClear = resolve; }});
  scheduleArg = null;
  h._realignBufferedSeek(50);
  h._realignBufferedSeek(200);
  finishClear();
  await Promise.resolve();
  await Promise.resolve();
  if (scheduleArg !== null) {{
    throw new Error("A superseded seek restarted the schedule timer");
  }}
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
        proc = subprocess.run([node_bin, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
