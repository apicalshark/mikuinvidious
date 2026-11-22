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
PLAYER_UI_JS = ROOT / "static" / "themes" / "modern" / "js" / "player_ui.js"


class PlayerReplayTests(unittest.TestCase):
    def setUp(self):
        self.src = PLAYER_JS.read_text(encoding="utf-8")
        self.ui_src = PLAYER_UI_JS.read_text(encoding="utf-8")

    def test_zero_seek_clamp_present(self):
        self.assertIn("_zeroSeekClamped", self.src)
        self.assertIn("currentTime", self.src)
        self.assertIn("!window.is_live && !video._zeroSeekClamped", self.src)

    def test_replay_handler_present(self):
        self.assertIn("_playReplayWrapped", self.src)
        self.assertIn("this.ended", self.src)
        self.assertIn("window.MikuPlayerUI", self.ui_src)

    def test_js_node_execution(self):
        node_bin = shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            brew_node = "/home/linuxbrew/.linuxbrew/bin/node"
            if os.path.exists(brew_node) and os.access(brew_node, os.X_OK):
                node_bin = brew_node
        if not node_bin:
            raise unittest.SkipTest("node executable not found")
        test_script = """
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

const video = {
  _currentTime: 120,
  duration: 120,
  ended: true,
  paused: true,
};
Object.setPrototypeOf(video, globalThis.HTMLMediaElement.prototype);

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

video.ended = false;
video.currentTime = 0;
if (video.currentTime !== 0.1) {
  throw new Error("Dragging to beginning should clamp to 0.1s, got: " + video.currentTime);
}

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
        self.assertIn("setPointerCapture", self.ui_src)
        self.assertIn("pendingSeekTime", self.ui_src)
        self.assertIn("finishScrub", self.ui_src)

        node_bin = shutil.which("node") or shutil.which("nodejs")
        if not node_bin:
            brew_node = "/home/linuxbrew/.linuxbrew/bin/node"
            if os.path.exists(brew_node) and os.access(brew_node, os.X_OK):
                node_bin = brew_node
        if not node_bin:
            raise unittest.SkipTest("node executable not found")

        test_script = f"""
const fs = require("fs");
const {{ EventTarget }} = globalThis;

class MockEl extends EventTarget {{
  constructor() {{
    super();
    this.attrs = new Map();
    this.style = {{}};
    this.textContent = "";
    this.offsetWidth = 40;
    this.classList = {{
      _set: new Set(),
      add(...c) {{ c.forEach((x) => this._set.add(x)); }},
      remove(...c) {{ c.forEach((x) => this._set.delete(x)); }},
      toggle(c, force) {{
        if (force === undefined) {{
          if (this._set.has(c)) this._set.delete(c); else this._set.add(c);
        }} else if (force) {{
          this._set.add(c);
        }} else {{
          this._set.delete(c);
        }}
      }},
      contains(c) {{ return this._set.has(c); }},
    }};
  }}
  setAttribute(k, v) {{ this.attrs.set(k, String(v)); }}
  getAttribute(k) {{ return this.attrs.get(k) ?? null; }}
  removeAttribute(k) {{ this.attrs.delete(k); }}
  hasAttribute(k) {{ return this.attrs.has(k); }}
  getBoundingClientRect() {{ return {{ left: 0, right: 200, top: 0, bottom: 20, width: 200, height: 20 }}; }}
  setPointerCapture() {{}}
  releasePointerCapture() {{}}
  hasPointerCapture() {{ return false; }}
}}

const scrubber = new MockEl();
const fill = new MockEl();
const thumb = new MockEl();
const tooltip = new MockEl();
const bottomBar = new MockEl();
const root = new MockEl();
const map = {{
  "[data-scrubber]": scrubber,
  "[data-scrubber-fill]": fill,
  "[data-scrubber-thumb]": thumb,
  "[data-scrubber-tooltip]": tooltip,
  "[data-skin-bottom]": bottomBar,
}};
root.querySelector = (sel) => map[sel] || null;
root.querySelectorAll = () => [];
root.contains = () => false;

const doc = new EventTarget();
doc.getElementById = () => null;
doc.fullscreenElement = null;
doc.pictureInPictureEnabled = false;
globalThis.document = doc;
globalThis.Element = MockEl;
globalThis.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
globalThis.window = {{ I18n: {{ t: (k) => k }} }};

const committed = [];
const video = new MockEl();
let _time = 10;
Object.defineProperty(video, "currentTime", {{
  get() {{ return _time; }},
  set(v) {{ _time = v; committed.push(v); }},
}});
video.duration = 100;
video.paused = false;
video.ended = false;
video.volume = 1;
video.muted = false;
video.buffered = {{ length: 0 }};
video.play = () => Promise.resolve();
video.pause = () => {{ video.paused = true; }};

eval(fs.readFileSync({str(PLAYER_UI_JS)!r}, "utf8"));
window.MikuPlayerUI.mount(root, video, {{ isLive: false }});

const mkEvt = (type, clientX) => {{
  const e = new Event(type);
  e.clientX = clientX;
  e.pointerId = 1;
  e.pointerType = "mouse";
  e.stopPropagation = () => {{}};
  return e;
}};

scrubber.dispatchEvent(mkEvt("pointerdown", 50));
scrubber.dispatchEvent(mkEvt("pointermove", 100));
scrubber.dispatchEvent(mkEvt("pointermove", 150));

if (committed.length !== 0) {{
  throw new Error("Expected 0 seeks during drag, got: " + JSON.stringify(committed));
}}
if (fill.style.width !== "75%") {{
  throw new Error("Expected visual preview at 75%, got: " + fill.style.width);
}}

scrubber.dispatchEvent(mkEvt("pointerup", 150));
if (committed.length !== 1 || committed[0] !== 75) {{
  throw new Error("Expected single committed seek [75] on pointerup, got: " + JSON.stringify(committed));
}}

window.MikuPlayerUI.destroy();
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
