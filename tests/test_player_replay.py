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
const spinner = new MockEl();
const buffer = new MockEl();
const volume = new MockEl();
const thumb = new MockEl();
const tooltip = new MockEl();
const bottomBar = new MockEl();
const root = new MockEl();
const map = {{
  "[data-scrubber]": scrubber,
  "[data-scrubber-fill]": fill,
  "[data-player-spinner]": spinner,
  "[data-scrubber-buffer]": buffer,
  "[data-volume-slider]": volume,
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

const mkEvt = (type, clientX, pointerId = 1) => {{
  const e = new Event(type);
  e.clientX = clientX;
  e.pointerId = pointerId;
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

for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {{
  scrubber.dispatchEvent(mkEvt(type, 0, 2));
}}
video.buffered = {{
  length: 2,
  start: (i) => [0, 60][i],
  end: (i) => [20, 80][i],
}};
video.dispatchEvent(new Event("durationchange"));
if (fill.style.width !== "75%" || buffer.style.width !== "20%" || committed.length !== 0) {{
  throw new Error("Expected secondary pointers and duration changes to preserve the pending seek");
}}

scrubber.dispatchEvent(mkEvt("pointerup", 150));
if (committed.length !== 1 || committed[0] !== 75) {{
  throw new Error("Expected single committed seek [75] on pointerup, got: " + JSON.stringify(committed));
}}

video.dispatchEvent(new Event("timeupdate"));
if (buffer.style.left !== "60%" || buffer.style.width !== "20%") {{
  throw new Error("Expected only the buffered interval containing the playhead to be displayed");
}}
video.currentTime = 40;
video.dispatchEvent(new Event("timeupdate"));
if (buffer.style.width !== "0%") throw new Error("Expected no buffer indicator in a gap");

volume.dispatchEvent(mkEvt("pointerdown", 100));
for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {{
  volume.dispatchEvent(mkEvt(type, 0, 2));
}}
if (video.volume !== 0.5) throw new Error("Expected the second pointer to leave volume unchanged");
volume.dispatchEvent(mkEvt("pointermove", 150));
if (video.volume !== 0.75) throw new Error("Expected the initiating pointer to still own volume");
volume.dispatchEvent(mkEvt("pointercancel", 150));
volume.dispatchEvent(mkEvt("pointerdown", 50, 2));
if (video.volume !== 0.25) throw new Error("Expected cancel to release volume ownership");

const stallCallbacks = new Map();
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
globalThis.setTimeout = (fn, delay) => {{
  if (delay !== 400) return realSetTimeout(fn, delay);
  const id = Symbol();
  stallCallbacks.set(id, fn);
  return id;
}};
globalThis.clearTimeout = (id) => {{
  if (typeof id === "symbol") stallCallbacks.delete(id);
  else realClearTimeout(id);
}};

video.dispatchEvent(new Event("playing"));
video.dispatchEvent(new Event("waiting"));
if (stallCallbacks.size !== 1 || spinner.classList.contains("flex")) {{
  throw new Error("Expected an active playback stall to delay showing the spinner");
}}
video.paused = true;
video.dispatchEvent(new Event("pause"));
if (stallCallbacks.size || spinner.classList.contains("flex")) {{
  throw new Error("Expected pause to clear the scheduled spinner");
}}
video.paused = false;
video.dispatchEvent(new Event("waiting"));
const [stall, showSpinner] = stallCallbacks.entries().next().value;
stallCallbacks.delete(stall);
showSpinner();
if (!spinner.classList.contains("flex")) throw new Error("Expected spinner after active stall");
video.paused = true;
video.dispatchEvent(new Event("pause"));
if (spinner.classList.contains("flex")) throw new Error("Expected pause to hide a visible spinner");

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

    def test_settings_menu_danmaku_size_and_clickable_remaining_time(self):
        self.assertIn("setupVodMenu", self.ui_src)
        self.assertIn("Danmaku size", self.ui_src)
        self.assertIn("toggleTimeDisplayMode", self.ui_src)
        self.assertNotIn("ion-ios-arrow-forward", self.src)

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
  constructor(tag = "div") {{
    super();
    this.tagName = tag.toUpperCase();
    this.attrs = new Map();
    this.dataset = {{}};
    this.style = {{}};
    this.textContent = "";
    this.innerHTML = "";
    this.hidden = false;
    this.children = [];
    this.offsetWidth = 100;
    this.offsetHeight = 100;
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
  get innerHTML() {{ return this._innerHTML || ""; }}
  set innerHTML(v) {{
    this._innerHTML = String(v);
    if (v === "") this.children = [];
  }}
  setAttribute(k, v) {{
    this.attrs.set(k, String(v));
    if (k.startsWith("data-")) {{
      const camel = k.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      this.dataset[camel] = String(v);
    }}
  }}
  getAttribute(k) {{ return this.attrs.get(k) ?? null; }}
  focus() {{ document.activeElement = this; }}
  contains(el) {{ return el === this || this._all().includes(el); }}
  closest(sel) {{
    return sel.includes("a, button") && this.tagName === "BUTTON" ? this : null;
  }}
  removeAttribute(k) {{ this.attrs.delete(k); }}
  hasAttribute(k) {{ return this.attrs.has(k); }}
  appendChild(child) {{
    if (child && child.isFragment) {{
      child.children.forEach((c) => this.children.push(c));
    }} else if (child) {{
      this.children.push(child);
    }}
    return child;
  }}
  getBoundingClientRect() {{ return {{ left: 0, right: 200, top: 0, bottom: 20, width: 200, height: 20 }}; }}
  _all() {{
    const out = [];
    const walk = (node) => {{
      for (const ch of node.children || []) {{
        out.push(ch);
        walk(ch);
      }}
    }};
    walk(this);
    return out;
  }}
  querySelector(sel) {{ return this.querySelectorAll(sel)[0] || null; }}
  querySelectorAll(sel) {{
    return this._all().filter((el) => {{
      if (sel === "[data-menu-view]") return el.dataset && el.dataset.menuView != null;
      if (sel === "[data-menu-view]:not([hidden])") return el.dataset.menuView != null && !el.hidden;
      if (sel === "button:not(:disabled)") return el.tagName === "BUTTON" && !el.disabled;
      if (sel === "[data-danmaku-size-value]") return el.hasAttribute("data-danmaku-size-value");
      if (sel === "[data-resolution-value]") return el.hasAttribute("data-resolution-value");
      if (sel === "[data-speed-value]") return el.hasAttribute("data-speed-value");
      if (sel === "button[data-danmaku-size]") return el.tagName === "BUTTON" && el.dataset.danmakuSize != null;
      if (sel === "button[data-video-qn]") return el.tagName === "BUTTON" && el.dataset.videoQn != null;
      if (sel === "button[data-video-qn].active") return el.tagName === "BUTTON" && el.dataset.videoQn != null && el.classList.contains("active");
      if (sel === "button[data-speed-rate]") return el.tagName === "BUTTON" && el.dataset.speedRate != null;
      if (sel === "span") return el.tagName === "SPAN";
      return false;
    }});
  }}
}}

const timeCurrent = new MockEl("span");
const timeDuration = new MockEl("span");
const timeBtn = new MockEl("button");
const settingsBtn = new MockEl("button");
const settingsMenu = new MockEl("div");
const settingsList = new MockEl("div");
const bottomBar = new MockEl("div");
const root = new MockEl("div");

const rootMap = {{
  "[data-time-current]": timeCurrent,
  "[data-time-duration]": timeDuration,
  "#quality-btn": settingsBtn,
  "#quality-menu": settingsMenu,
  "#quality-list": settingsList,
  "[data-skin-bottom]": bottomBar,
}};
root.querySelector = (sel) => rootMap[sel] || null;
root.querySelectorAll = (sel) => (sel === "[data-time-toggle]" ? [timeBtn] : []);
root.appendChild(settingsBtn);
root.appendChild(settingsMenu);
settingsMenu.appendChild(settingsList);
root.getBoundingClientRect = () => ({{ right: 600, bottom: 220, top: 0, height: 220 }});
settingsBtn.getBoundingClientRect = () => ({{ right: 590, top: 170 }});

const store = new Map([["miku_danmaku_size", "1"]]);
let failWrites = false;
globalThis.localStorage = {{
  getItem: (k) => store.get(k) ?? null,
  setItem: (k, v) => {{
    if (failWrites) throw new Error("Storage is unavailable");
    store.set(k, String(v));
  }},
}};

const doc = new EventTarget();
doc.getElementById = () => null;
doc.createElement = (tag) => new MockEl(tag);
doc.createDocumentFragment = () => ({{ isFragment: true, children: [], appendChild(c) {{ this.children.push(c); }} }});
globalThis.document = doc;
globalThis.Element = MockEl;
globalThis.window = {{
  I18n: {{ t: (k) => k }},
  getComputedStyle: () => ({{ paddingTop: "6px", paddingBottom: "6px" }}),
}};

const video = new MockEl("video");
video.currentTime = 30;
video.duration = 120;
video.paused = false;
video.ended = false;
video.volume = 1;
video.muted = false;
video.playbackRate = 1;
video.buffered = {{ length: 0 }};

eval(fs.readFileSync({str(PLAYER_UI_JS)!r}, "utf8"));
window.MikuPlayerUI.mount(root, video, {{ isLive: false }});

if (timeCurrent.textContent !== "0:30" || timeDuration.textContent !== "2:00") {{
  throw new Error("Expected initial time 0:30 / 2:00, got: " + timeCurrent.textContent + " / " + timeDuration.textContent);
}}

const clickEvt = new Event("click");
clickEvt.stopPropagation = () => {{}};
timeBtn.dispatchEvent(clickEvt);
if (timeDuration.textContent !== "-1:30") {{
  throw new Error("Expected remaining time -1:30 after click, got: " + timeDuration.textContent);
}}

timeBtn.dispatchEvent(clickEvt);
if (timeDuration.textContent !== "2:00") {{
  throw new Error("Expected total duration 2:00 after second click, got: " + timeDuration.textContent);
}}

const activeComment = {{
  text: "flying",
  style: {{ fontSize: "25px", font: "25px sans-serif" }},
  width: 120,
  height: 28,
  node: {{ style: {{}}, offsetWidth: 150, offsetHeight: 35 }},
}};
const queuedComment = {{
  text: "queued",
  style: {{ fontSize: "25px", font: "25px sans-serif" }},
  width: 100,
  height: 28,
}};
const Danmaku = require({str(ROOT / "static" / "danmakujs" / "danmaku.min.js")!r});
const dmMedia = new MockEl("video");
dmMedia.paused = true;
dmMedia.currentTime = 30;
window.dm = new Danmaku({{
  media: dmMedia,
  comments: [activeComment, queuedComment],
}});
window.dm._.runningList.push(activeComment);
window.MikuPlayerUI.applyDanmakuScale(window.dm);
video.src = "https://example.test/proxy/video/BVexample_32_80.mp4?quality=_32";
window.MikuPlayerUI.setupVodMenu({{
  sources: [{{ quality: 80, new_description: "1080P" }}, {{ quality: 32, new_description: "480P" }}],
  isDash: false,
}});

const keydown = (receiver, target, key) => {{
  const e = new Event("keydown", {{ cancelable: true }});
  e.key = key;
  Object.defineProperty(e, "target", {{ value: target }});
  receiver.dispatchEvent(e);
  return e.defaultPrevented;
}};
settingsBtn.dispatchEvent(clickEvt);
const main = settingsList.querySelector("[data-menu-view]:not([hidden])");
const [resolution, speed] = main.querySelectorAll("button:not(:disabled)");
if (settingsList.style.maxHeight !== "148px" || doc.activeElement !== resolution) {{
  throw new Error("Expected settings to fit above the controls and focus its first row");
}}
if (!keydown(settingsMenu, resolution, "ArrowDown") || doc.activeElement !== speed) {{
  throw new Error("Expected ArrowDown to focus the next row");
}}
speed.dispatchEvent(clickEvt);
const speedView = settingsList.querySelector("[data-menu-view]:not([hidden])");
const [back] = speedView.querySelectorAll("button:not(:disabled)");
if (speedView.dataset.menuView !== "speed" || doc.activeElement !== back) {{
  throw new Error("Expected focus to move into the speed view");
}}
back.dispatchEvent(clickEvt);
if (doc.activeElement !== speed) throw new Error("Expected Back to restore focus to Playback speed");
window.MikuPlayerUI.closeSettings();
if (doc.activeElement !== settingsBtn) throw new Error("Expected focus to return to Settings");
root.getBoundingClientRect = () => ({{ right: 600, bottom: 600, top: 0, height: 600 }});
settingsBtn.dispatchEvent(clickEvt);
if (settingsList.style.maxHeight !== "148px") {{
  throw new Error("Expected the menu to stay above the controls when their offset changes");
}}
window.MikuPlayerUI.closeSettings();
settingsBtn.getBoundingClientRect = () => ({{ right: 590, top: 550 }});
settingsBtn.dispatchEvent(clickEvt);
if (settingsList.style.maxHeight !== "320px") throw new Error("Expected the 320px cap");
window.MikuPlayerUI.closeSettings();

if (keydown(doc, new MockEl(), "ArrowUp") || video.volume !== 1) {{
  throw new Error("Expected outside focus to preserve default ArrowUp");
}}
for (const key of [" ", "Enter"]) {{
  if (keydown(doc, settingsBtn, key)) throw new Error("Expected native button activation");
}}
if (!keydown(doc, root, "ArrowRight") || video.currentTime !== 35) {{
  throw new Error("Expected ArrowRight to seek with focus inside a VOD player");
}}

const checkedQualities = settingsList.querySelectorAll("button[data-video-qn]")
  .filter((b) => b.getAttribute("aria-checked") === "true");
if (checkedQualities.length !== 1 || checkedQualities[0].dataset.videoQn !== "80") {{
  throw new Error("Expected only quality 80 to match the selected source");
}}

const dv = settingsList.querySelector("[data-danmaku-size-value]");
if (!dv || dv.textContent !== "Medium") {{
  throw new Error("Expected default danmaku size Medium, got: " + (dv && dv.textContent));
}}
if (activeComment.style.fontSize !== "25px" || activeComment.width !== 120) {{
  throw new Error("Expected Medium initial load to leave active comment untouched");
}}

const sizeBtns = settingsList.querySelectorAll("button[data-danmaku-size]");
const largeBtn = sizeBtns.find((b) => b.dataset.danmakuSize === "1.25");
const mediumBtn = sizeBtns.find((b) => b.dataset.danmakuSize === "1");
if (!largeBtn || !mediumBtn) throw new Error("Danmaku size buttons not found");
failWrites = true;
largeBtn.dispatchEvent(clickEvt);
if (largeBtn.getAttribute("aria-checked") !== "true" || store.get("miku_danmaku_size") !== "1") {{
  throw new Error("Expected Large to stay checked even when storage rejects the update");
}}

if (dv.textContent !== "Large") {{
  throw new Error("Expected danmaku size Large after click, got: " + dv.textContent);
}}
if (activeComment.style.fontSize !== "31px" || queuedComment.style.fontSize !== "31px") {{
  throw new Error("Expected scaled fontSize 31px for Large, got: " + activeComment.style.fontSize);
}}
if (activeComment.width !== 150 || activeComment.height !== 35) {{
  throw new Error("Expected active runningList comment to remeasure width/height instead of deleting");
}}
if (queuedComment.width !== undefined || queuedComment.height !== undefined) {{
  throw new Error("Expected queued comment width/height to be invalidated for fresh measurement");
}}

const emitted = {{
  text: "emitted while Large",
  style: {{ fontSize: "20px", font: "bold 20px serif" }},
}};
window.dm.emit(emitted);
const retained = window.dm.comments.find((c) => c.text === emitted.text);
if (!retained || retained === emitted || retained.style.font !== "bold 25px serif") {{
  throw new Error("Expected Danmaku to retain a copy scaled to Large");
}}

mediumBtn.dispatchEvent(clickEvt);
if (retained.style.fontSize !== "20px" || retained.style.font !== "bold 20px serif") {{
  throw new Error("Expected the retained copy to restore its exact original font at Medium");
}}
sizeBtns.find((b) => b.dataset.danmakuSize === "0.8").dispatchEvent(clickEvt);
if (retained.style.fontSize !== "16px" || retained.style.font !== "bold 16px serif") {{
  throw new Error("Expected Small to scale the retained copy from its original baseline");
}}
mediumBtn.dispatchEvent(clickEvt);
if (activeComment.style.fontSize !== "25px" || activeComment.style.font !== "25px sans-serif") {{
  throw new Error("Expected Medium to restore exact original font, got: " + activeComment.style.font);
}}

window.MikuPlayerUI.mount(root, video, {{ isLive: true }});
if (keydown(doc, root, "ArrowRight") || video.currentTime !== 35) {{
  throw new Error("Expected live playback to preserve default ArrowRight");
}}
window.MikuPlayerUI.setupLiveMenu({{ items: [] }});
if (settingsList.querySelector("[data-resolution-value]")) {{
  throw new Error("Expected Resolution row to be omitted when no live qualities are available");
}}
const liveDv = settingsList.querySelector("[data-danmaku-size-value]");
if (!liveDv || liveDv.textContent !== "Medium") {{
  throw new Error("Expected Danmaku size row in live settings menu without quality items");
}}
window.MikuPlayerUI.destroy();
"""
        proc = subprocess.run([node_bin, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
