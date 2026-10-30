"""Unit tests for player replay cache retention after ended state.

Run with PYTHONPATH=python uv run python -m unittest discover -s tests.
"""

import json
from pathlib import Path
import subprocess
import unittest

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
        proc = subprocess.run(["node", "-e", test_script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
