(function () {
  const IDLE_MS = 2500;
  const STALL_DELAY_MS = 400;
  const HUD_DURATION_MS = 650;
  const RAPID_SEEK_WINDOW_MS = 1000;
  const MOUSE_CLICK_DELAY_MS = 220;
  const TOUCH_DOUBLE_TAP_MS = 280;
  const VOLUME_STORAGE_KEY = "miku_player_volume";
  const MUTED_STORAGE_KEY = "miku_player_muted";
  const THEATER_STORAGE_KEY = "miku_player_theater";
  const TIME_REMAINING_STORAGE_KEY = "miku_player_time_remaining";
  const DANMAKU_SIZE_STORAGE_KEY = "miku_danmaku_size";

  const DANMAKU_SIZES = [
    { key: "0.8", scale: 0.8 },
    { key: "1", scale: 1 },
    { key: "1.25", scale: 1.25 },
  ];
  const DEFAULT_DANMAKU_SIZE_KEY = "0.8";
  const danmakuFontBaselines = new WeakMap();

  function getDanmakuSizeLabel(key) {
    if (key === "0.8") return t("Small");
    if (key === "1.25") return t("Large");
    return t("Medium");
  }

  const HUD_ICONS = {
    play: '<svg class="size-5 fill-current" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.14v14l11-7-11-7z"/></svg>',
    pause:
      '<svg class="size-5 fill-current" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>',
    seekBack:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
    seekForward:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
    volume:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>',
    muted:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/></svg>',
    captions:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 15h4M15 15h2M7 11h2M13 11h4"/></svg>',
    danmaku:
      '<svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="13" y2="13"/></svg>',
  };

  const MENU_ICONS = {
    resolution:
      '<svg class="size-4 shrink-0 text-white/75" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
    audio:
      '<svg class="size-4 shrink-0 text-white/75" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/></svg>',
    speed:
      '<svg class="size-4 shrink-0 text-white/75" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>',
    danmaku:
      '<svg class="size-4 shrink-0 text-white/75" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="13" y2="13"/></svg>',
    chevronRight:
      '<svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
    chevronLeft:
      '<svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
    check:
      '<svg class="size-4 shrink-0 opacity-0 transition-opacity group-[.active]:opacity-100" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
  };

  function t(key, params) {
    if (typeof window.I18n !== "undefined" && typeof window.I18n.t === "function") {
      return window.I18n.t(key, params);
    }
    return key;
  }

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const total = Math.floor(seconds);
    const hrs = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const paddedSecs = secs < 10 ? "0" + secs : String(secs);
    if (hrs > 0) {
      const paddedMins = mins < 10 ? "0" + mins : String(mins);
      return hrs + ":" + paddedMins + ":" + paddedSecs;
    }
    return mins + ":" + paddedSecs;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function storageGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {}
  }

  function getStoredDanmakuSizeEntry() {
    const raw = storageGet(DANMAKU_SIZE_STORAGE_KEY);
    return (
      DANMAKU_SIZES.find((entry) => entry.key === raw) ||
      DANMAKU_SIZES.find((entry) => entry.key === DEFAULT_DANMAKU_SIZE_KEY) ||
      DANMAKU_SIZES[0]
    );
  }

  function getDanmakuScale() {
    if (
      typeof window.dm_scale === "number" &&
      Number.isFinite(window.dm_scale) &&
      window.dm_scale > 0
    ) {
      return window.dm_scale;
    }
    const entry = getStoredDanmakuSizeEntry();
    window.dm_scale = entry.scale;
    return entry.scale;
  }

  function scaleDanmakuComment(c, scale, isRunning) {
    const style = c?.style;
    if (!style || typeof style !== "object") return;
    let baseline = danmakuFontBaselines.get(style);
    if (!baseline) {
      if (scale === 1) return;
      const parsed = parseFloat(style.fontSize);
      baseline = {
        size: Number.isFinite(parsed) && parsed > 0 ? parsed : 25,
        fontSize: style.fontSize,
        font: style.font,
      };
      danmakuFontBaselines.set(style, baseline);
    }
    if (scale === 1) {
      if (baseline.fontSize === undefined) delete style.fontSize;
      else style.fontSize = baseline.fontSize;
      if (baseline.font === undefined) delete style.font;
      else style.font = baseline.font;
    } else {
      const px = Math.max(12, Math.round(baseline.size * scale)) + "px";
      style.fontSize = px;
      style.font =
        typeof baseline.font === "string" && baseline.font
          ? baseline.font.replace(/\d+(?:\.\d+)?px/, px)
          : px + " sans-serif";
    }
    if (c.node && c.node.style) {
      if (c.style.font) c.node.style.font = c.style.font;
      if (c.style.fontSize) c.node.style.fontSize = c.style.fontSize;
    }
    if (isRunning) {
      if (c.node) {
        const nextW = c.node.offsetWidth;
        const nextH = c.node.offsetHeight;
        if (nextW > 0) c.width = nextW;
        if (nextH > 0) c.height = nextH;
      }
    } else {
      delete c.width;
      delete c.height;
    }
  }

  function applyDanmakuScaleToInstance(dm) {
    const target = dm || window.dm;
    const scale = getDanmakuScale();
    if (!target) return scale;
    const runningList = Array.isArray(target._?.runningList) ? target._.runningList : [];
    const runningSet = new Set(runningList);
    if (Array.isArray(target.comments)) {
      for (let i = 0; i < target.comments.length; i++) {
        const c = target.comments[i];
        scaleDanmakuComment(c, scale, runningSet.has(c));
      }
    }
    if (!target._mikuEmitWrapped && typeof target.emit === "function") {
      const origEmit = target.emit;
      target.emit = function (obj) {
        if (obj && typeof obj === "object") {
          scaleDanmakuComment(obj, getDanmakuScale(), false);
        }
        return origEmit.call(this, obj);
      };
      target._mikuEmitWrapped = true;
    }
    return scale;
  }

  function blocksPlayerShortcut(event, root) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return true;
    const target = event.target;
    if (!(target instanceof Element) || !root.contains(target)) return true;
    if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) {
      return true;
    }
    if (target.closest("[role='menu'], [role='menuitem'], [data-volume-slider]")) {
      return true;
    }
    const interactive = target.closest("a, button, [role='button'], [role='slider'], summary");
    if (interactive && (event.key === " " || event.key === "Enter")) {
      return true;
    }
    return false;
  }

  let activeInstance = null;

  function createController(root, video, opts) {
    const isLive = Boolean(opts && opts.isLive);
    const isAudio = Boolean(opts && opts.isAudio);
    const ac = new AbortController();
    const signal = ac.signal;

    const gestureLayer = root.querySelector("[data-player-gesture-layer]");
    const spinner = root.querySelector("[data-player-spinner]");
    const hudPill = root.querySelector("[data-player-hud-pill]");
    const hudIcon = root.querySelector("[data-player-hud-icon]");
    const hudText = root.querySelector("[data-player-hud-text]");
    const bottomBar = root.querySelector("[data-skin-bottom]");
    const playBtn = root.querySelector("[data-play-btn]");
    const iconPlay = root.querySelector("[data-icon-play]");
    const iconPause = root.querySelector("[data-icon-pause]");
    const iconReplay = root.querySelector("[data-icon-replay]");
    const volumeGroup = root.querySelector("[data-volume-group]");
    const muteBtn = root.querySelector("[data-mute-btn]");
    const iconVolHigh = root.querySelector("[data-icon-vol-high]");
    const iconVolLow = root.querySelector("[data-icon-vol-low]");
    const iconVolMuted = root.querySelector("[data-icon-vol-muted]");
    const volumeSlider = root.querySelector("[data-volume-slider]");
    const volumeFill = root.querySelector("[data-volume-fill]");
    const volumeThumb = root.querySelector("[data-volume-thumb]");
    const liveBtn = root.querySelector("[data-live-btn]");
    const timeCurrent = root.querySelector("[data-time-current]");
    const timeDuration = root.querySelector("[data-time-duration]");
    const timeCurrentCompact = root.querySelector("[data-time-current-compact]");
    const timeDurationCompact = root.querySelector("[data-time-duration-compact]");
    const timeToggleBtns = root.querySelectorAll("[data-time-toggle]");
    const scrubber = root.querySelector("[data-scrubber]");
    const scrubberBuffer = root.querySelector("[data-scrubber-buffer]");
    const scrubberFill = root.querySelector("[data-scrubber-fill]");
    const scrubberThumb = root.querySelector("[data-scrubber-thumb]");
    const scrubberTooltip = root.querySelector("[data-scrubber-tooltip]");
    const danmakuBtn = root.querySelector("#danmaku-toggle");
    const captionsSelect = root.querySelector("[data-captions-select]");
    const renderedSubtitles =
      captionsSelect && typeof video.querySelectorAll === "function"
        ? Array.from(
            video.querySelectorAll("track[kind='subtitles'], track[kind='captions']"),
            (el) => el.track
          )
        : [];
    const settingsBtn = root.querySelector("#quality-btn");
    const settingsMenu = root.querySelector("#quality-menu");
    const settingsList = root.querySelector("#quality-list");
    const pipBtn = root.querySelector("[data-pip-btn]");
    const theaterBtn = root.querySelector("[data-theater-btn]");
    const iconTheaterEnter = root.querySelector("[data-icon-theater-enter]");
    const iconTheaterExit = root.querySelector("[data-icon-theater-exit]");
    const fullscreenBtn = root.querySelector("[data-fullscreen-btn]");
    const iconFsEnter = root.querySelector("[data-icon-fs-enter]");
    const iconFsExit = root.querySelector("[data-icon-fs-exit]");

    let idleTimer = null;
    let stallTimer = null;
    let hudTimer = null;
    let clickTimer = null;
    let lastTapAt = 0;
    let lastTapRegion = null;
    let pointerInBar = false;
    let scrubPointerId = null;
    let pendingSeekTime = null;
    let volumePointerId = null;
    let hasPlayedOnce = false;
    let settingsOpen = false;
    let menuInvoker = null;
    let lastSeekAt = 0;
    let seekAccum = 0;
    let savedVolume = 1;
    let showRemainingTime = storageGet(TIME_REMAINING_STORAGE_KEY) === "1";

    getDanmakuScale();

    const storedVol = parseFloat(storageGet(VOLUME_STORAGE_KEY));
    if (Number.isFinite(storedVol) && storedVol >= 0 && storedVol <= 1) {
      video.volume = storedVol;
      if (storedVol > 0) savedVolume = storedVol;
    }
    const storedMuted = storageGet(MUTED_STORAGE_KEY);
    if (storedMuted === "1") {
      video.muted = true;
    }

    function setBtnLabel(btn, text) {
      if (!btn) return;
      btn.setAttribute("aria-label", text);
      btn.setAttribute("title", text);
    }

    function showHud(iconName, text) {
      if (!hudPill || !hudIcon || !hudText) return;
      if (hudTimer) clearTimeout(hudTimer);
      hudIcon.innerHTML = HUD_ICONS[iconName] || "";
      hudIcon.classList.toggle("hidden", !HUD_ICONS[iconName]);
      hudText.textContent = text || "";
      hudText.classList.toggle("hidden", !text);
      hudPill.removeAttribute("data-hud-active");
      void hudPill.offsetWidth;
      hudPill.setAttribute("data-hud-active", "");
      hudTimer = setTimeout(() => {
        hudPill.removeAttribute("data-hud-active");
        hudTimer = null;
      }, HUD_DURATION_MS);
    }

    function setSpinnerVisible(visible) {
      if (!spinner) return;
      spinner.classList.toggle("hidden", !visible);
      spinner.classList.toggle("flex", visible);
    }

    function clearStallTimer() {
      if (stallTimer) {
        clearTimeout(stallTimer);
        stallTimer = null;
      }
    }

    function showControls(resetTimer) {
      if (!bottomBar) return;
      bottomBar.setAttribute("data-visible", "");
      root.removeAttribute("data-idle");
      if (resetTimer !== false) scheduleIdle();
    }

    function hideControls() {
      if (!bottomBar) return;
      if (typeof root.matches === "function" && root.matches(":focus-visible")) {
        showControls(false);
        return;
      }
      if (
        video.paused ||
        video.ended ||
        settingsOpen ||
        scrubPointerId !== null ||
        volumePointerId !== null ||
        pointerInBar
      ) {
        return;
      }
      bottomBar.removeAttribute("data-visible");
      root.setAttribute("data-idle", "");
    }

    function scheduleIdle() {
      if (idleTimer) {
        clearTimeout(idleTimer);
        idleTimer = null;
      }
      if (
        video.paused ||
        video.ended ||
        settingsOpen ||
        scrubPointerId !== null ||
        volumePointerId !== null ||
        pointerInBar
      ) {
        return;
      }
      idleTimer = setTimeout(() => {
        idleTimer = null;
        hideControls();
      }, IDLE_MS);
    }

    function syncPlayState() {
      const ended = Boolean(
        video.ended || (!isLive && video.duration > 1 && video.currentTime >= video.duration)
      );
      const paused = Boolean(video.paused);
      if (iconPlay) iconPlay.classList.toggle("hidden", ended || !paused);
      if (iconPause) iconPause.classList.toggle("hidden", ended || paused);
      if (iconReplay) iconReplay.classList.toggle("hidden", !ended);
      setBtnLabel(playBtn, ended ? t("Replay") : paused ? t("Play") : t("Pause"));
      if (paused || ended) {
        showControls(false);
      } else {
        scheduleIdle();
      }
    }

    function togglePlay(withHud) {
      if (video.paused || video.ended) {
        const p = video.play();
        if (p && typeof p.catch === "function") p.catch(() => {});
        if (withHud) showHud("play", "");
      } else {
        video.pause();
        if (withHud) showHud("pause", "");
      }
    }

    function syncVolumeState() {
      const effective = video.muted ? 0 : clamp(video.volume, 0, 1);
      const pct = Math.round(effective * 100);
      if (iconVolMuted) iconVolMuted.classList.toggle("hidden", effective > 0);
      if (iconVolLow) iconVolLow.classList.toggle("hidden", effective === 0 || effective >= 0.5);
      if (iconVolHigh) iconVolHigh.classList.toggle("hidden", effective < 0.5);
      setBtnLabel(muteBtn, effective === 0 ? t("Unmute") : t("Mute"));
      if (volumeFill) volumeFill.style.width = pct + "%";
      if (volumeThumb) volumeThumb.style.left = pct + "%";
      if (volumeSlider) {
        volumeSlider.setAttribute("aria-valuenow", String(pct));
        volumeSlider.setAttribute("aria-valuetext", pct + "%");
      }
    }

    function setVolume(next, withHud) {
      const clamped = clamp(next, 0, 1);
      video.volume = clamped;
      if (clamped > 0) {
        savedVolume = clamped;
        video.muted = false;
      } else {
        video.muted = true;
      }
      storageSet(VOLUME_STORAGE_KEY, String(clamped));
      storageSet(MUTED_STORAGE_KEY, video.muted ? "1" : "0");
      syncVolumeState();
      if (withHud) {
        const pct = Math.round((video.muted ? 0 : clamped) * 100);
        showHud(pct === 0 ? "muted" : "volume", pct + "%");
      }
    }

    function toggleMute(withHud) {
      if (video.muted || video.volume === 0) {
        video.muted = false;
        if (video.volume === 0) {
          video.volume = savedVolume > 0 ? savedVolume : 0.5;
        }
      } else {
        savedVolume = video.volume;
        video.muted = true;
      }
      storageSet(VOLUME_STORAGE_KEY, String(video.volume));
      storageSet(MUTED_STORAGE_KEY, video.muted ? "1" : "0");
      syncVolumeState();
      if (withHud) {
        const pct = Math.round((video.muted ? 0 : video.volume) * 100);
        showHud(pct === 0 ? "muted" : "volume", pct + "%");
      }
    }

    function updateScrubberVisual(time, duration) {
      const safeDur = Number.isFinite(duration) && duration > 0 ? duration : 0;
      const safeTime = safeDur > 0 ? clamp(time, 0, safeDur) : 0;
      const pct = safeDur > 0 ? (safeTime / safeDur) * 100 : 0;
      if (scrubberFill) scrubberFill.style.width = pct + "%";
      if (scrubberThumb) scrubberThumb.style.left = pct + "%";
      const curStr = formatTime(safeTime);
      const totalStr = formatTime(safeDur);
      const remStr = "-" + formatTime(Math.max(0, safeDur - safeTime));
      const rightStr = showRemainingTime ? remStr : totalStr;
      if (timeCurrent) timeCurrent.textContent = curStr;
      if (timeCurrentCompact) timeCurrentCompact.textContent = curStr;
      if (timeDuration) timeDuration.textContent = rightStr;
      if (timeDurationCompact) timeDurationCompact.textContent = rightStr;
      if (scrubber) {
        scrubber.setAttribute("aria-valuemax", String(Math.floor(safeDur)));
        scrubber.setAttribute("aria-valuenow", String(Math.floor(safeTime)));
        scrubber.setAttribute("aria-valuetext", curStr + " / " + totalStr);
      }
    }

    function toggleTimeDisplayMode() {
      showRemainingTime = !showRemainingTime;
      storageSet(TIME_REMAINING_STORAGE_KEY, showRemainingTime ? "1" : "0");
      const cur =
        scrubPointerId !== null && pendingSeekTime !== null ? pendingSeekTime : video.currentTime;
      updateScrubberVisual(cur, video.duration);
    }

    function syncBufferBar() {
      if (!scrubberBuffer || isLive) return;
      const dur = video.duration;
      const cur = video.currentTime;
      const ranges = video.buffered;
      if (Number.isFinite(dur) && dur > 0 && ranges) {
        for (let i = 0; i < ranges.length; i++) {
          const start = ranges.start(i);
          const end = ranges.end(i);
          if (cur >= start && cur <= end) {
            const left = clamp((start / dur) * 100, 0, 100);
            const right = clamp((end / dur) * 100, left, 100);
            scrubberBuffer.style.left = left + "%";
            scrubberBuffer.style.width = right - left + "%";
            return;
          }
        }
      }
      scrubberBuffer.style.width = "0%";
    }

    function ratioFromPointer(el, clientX) {
      const rect = el.getBoundingClientRect();
      if (!rect.width) return 0;
      return clamp((clientX - rect.left) / rect.width, 0, 1);
    }

    function updateScrubberTooltip(ratio) {
      if (!scrubberTooltip || !scrubber) return;
      const dur = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
      scrubberTooltip.textContent = formatTime(ratio * dur);
      const trackWidth = scrubber.getBoundingClientRect().width;
      const tipWidth = scrubberTooltip.offsetWidth || 40;
      const half = tipWidth / 2;
      const px = clamp(ratio * trackWidth, half, Math.max(half, trackWidth - half));
      scrubberTooltip.style.left = px + "px";
    }

    function canSeek() {
      return !isLive && Number.isFinite(video.duration) && video.duration > 0;
    }

    function seekBy(delta, withHud) {
      if (!canSeek()) return;
      const now = performance.now();
      if (now - lastSeekAt < RAPID_SEEK_WINDOW_MS && Math.sign(delta) === Math.sign(seekAccum)) {
        seekAccum += delta;
      } else {
        seekAccum = delta;
      }
      lastSeekAt = now;
      const target = clamp(video.currentTime + delta, 0, video.duration);
      video.currentTime = target;
      updateScrubberVisual(target, video.duration);
      if (withHud) {
        const sign = seekAccum > 0 ? "+" : "";
        showHud(seekAccum < 0 ? "seekBack" : "seekForward", sign + seekAccum + "s");
      }
    }

    function enforceSingleSubtitle(preferredTrack) {
      const tracks = Array.from(video.textTracks || []).filter(
        (track) => track.kind === "subtitles" || track.kind === "captions"
      );
      const active =
        preferredTrack === undefined
          ? tracks.find((track) => track.mode === "showing")
          : preferredTrack;
      tracks.forEach((track) => {
        if (preferredTrack !== undefined || track.mode === "showing") {
          const mode = track === active ? "showing" : "disabled";
          if (track.mode !== mode) track.mode = mode;
        }
      });
      syncCaptionsState();
    }

    function syncCaptionsState() {
      if (!captionsSelect) return;
      captionsSelect.selectedIndex =
        renderedSubtitles.findIndex((track) => track.mode === "showing") + 1;
    }

    function toggleCaptions() {
      if (renderedSubtitles.length === 0) return;
      const showing = Array.from(video.textTracks || []).some(
        (track) =>
          (track.kind === "subtitles" || track.kind === "captions") && track.mode === "showing"
      );
      const preferredTrack = showing ? null : renderedSubtitles[0];
      enforceSingleSubtitle(preferredTrack);
      showHud("captions", preferredTrack ? preferredTrack.label || t("On") : t("Off"));
    }

    function visibleMenuItems() {
      const view = settingsList?.querySelector("[data-menu-view]:not([hidden])");
      return view
        ? Array.from(view.querySelectorAll("button:not(:disabled)")).filter(
            (b) => !b.closest(".hidden")
          )
        : [];
    }

    function switchMenuView(name, dir) {
      if (!settingsList) return;
      const reduce =
        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const prevW = settingsList.offsetWidth;
      const prevH = settingsList.offsetHeight;
      settingsList.querySelectorAll("[data-menu-view]").forEach((v) => {
        const on = v.dataset.menuView === name;
        v.hidden = !on;
        if (on) v.dataset.menuDir = dir || "forward";
      });
      if (!reduce && typeof settingsList.animate === "function") {
        const nextW = settingsList.offsetWidth;
        const nextH = settingsList.offsetHeight;
        if ((prevW > 0 || prevH > 0) && (nextW !== prevW || nextH !== prevH)) {
          settingsList.animate(
            [
              { width: prevW + "px", height: prevH + "px" },
              { width: nextW + "px", height: nextH + "px" },
            ],
            { duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" }
          );
        }
      }
      if (settingsOpen) {
        const items = visibleMenuItems();
        const next = dir === "back" && items.includes(menuInvoker) ? menuInvoker : items[0];
        if (dir === "back") menuInvoker = null;
        next?.focus();
      }
    }

    function makeMenuNavRow(iconKey, labelText, valueAttr, targetView, onBeforeOpen) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.className =
        "w-full flex items-center justify-between gap-4 rounded-xl px-3 py-2.5 text-xs font-medium text-white/90 hover:bg-white/12 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-white/70";
      const left = document.createElement("span");
      left.className = "flex items-center gap-2.5";
      const iconWrap = document.createElement("span");
      iconWrap.className = "inline-flex items-center justify-center";
      iconWrap.innerHTML = MENU_ICONS[iconKey] || "";
      left.appendChild(iconWrap);
      const labelSpan = document.createElement("span");
      labelSpan.textContent = labelText;
      left.appendChild(labelSpan);
      btn.appendChild(left);

      const right = document.createElement("span");
      right.className = "ml-auto flex items-center gap-1 text-white/55";
      const valSpan = document.createElement("span");
      valSpan.className = "max-w-28 truncate";
      valSpan.setAttribute(valueAttr, "1");
      right.appendChild(valSpan);
      const chevWrap = document.createElement("span");
      chevWrap.className = "inline-flex items-center justify-center";
      chevWrap.innerHTML = MENU_ICONS.chevronRight;
      right.appendChild(chevWrap);
      btn.appendChild(right);

      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (typeof onBeforeOpen === "function") onBeforeOpen();
        menuInvoker = btn;
        switchMenuView(targetView, "forward");
      });
      return btn;
    }

    function makeSubmenuHeader(titleText, targetView) {
      const frag = document.createDocumentFragment();
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.className =
        "w-full flex items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold text-white/80 hover:bg-white/12 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-white/70";
      const chev = document.createElement("span");
      chev.className = "inline-flex items-center justify-center";
      chev.innerHTML = MENU_ICONS.chevronLeft;
      btn.appendChild(chev);
      const txt = document.createElement("span");
      txt.textContent = titleText;
      btn.appendChild(txt);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        switchMenuView(targetView || "main", "back");
      });
      frag.appendChild(btn);
      const sep = document.createElement("div");
      sep.className = "my-1 h-px bg-white/10";
      frag.appendChild(sep);
      return frag;
    }

    function makeOptionButton(labelText, datasetMap, onClick) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitemradio");
      btn.className =
        "group w-full flex items-center justify-between gap-4 rounded-xl px-3 py-2 text-xs font-medium text-white/85 hover:bg-white/12 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-white/70";
      if (datasetMap) {
        Object.keys(datasetMap).forEach((k) => {
          btn.dataset[k] = String(datasetMap[k]);
        });
      }
      const span = document.createElement("span");
      span.className = "truncate";
      span.textContent = labelText;
      btn.appendChild(span);
      const checkWrap = document.createElement("span");
      checkWrap.className = "inline-flex items-center justify-center";
      checkWrap.innerHTML = MENU_ICONS.check;
      btn.appendChild(checkWrap);
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        onClick(btn);
      });
      return btn;
    }

    function syncSpeedMenu() {
      if (!settingsList) return;
      const cur = video.playbackRate || 1;
      settingsList.querySelectorAll("button[data-speed-rate]").forEach((b) => {
        const active = Number(b.dataset.speedRate) === cur;
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      const sv = settingsList.querySelector("[data-speed-value]");
      if (sv) sv.textContent = cur + "x";
    }

    function buildSpeedSubView() {
      const view = document.createElement("div");
      view.dataset.menuView = "speed";
      view.className = "flex flex-col gap-0.5";
      view.hidden = true;
      view.appendChild(makeSubmenuHeader(t("Playback speed"), "main"));
      for (const rate of [2, 1.5, 1.25, 1, 0.75, 0.5]) {
        const btn = makeOptionButton(rate + "x", { speedRate: rate }, () => {
          video.playbackRate = rate;
          syncSpeedMenu();
          closeSettings();
        });
        view.appendChild(btn);
      }
      return view;
    }

    function syncDanmakuSizeMenu() {
      if (!settingsList) return;
      const scale = getDanmakuScale();
      const key =
        DANMAKU_SIZES.find((entry) => entry.scale === scale)?.key ?? DEFAULT_DANMAKU_SIZE_KEY;
      settingsList.querySelectorAll("button[data-danmaku-size]").forEach((b) => {
        const active = b.dataset.danmakuSize === key;
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      const dv = settingsList.querySelector("[data-danmaku-size-value]");
      if (dv) dv.textContent = getDanmakuSizeLabel(key);
    }

    function setDanmakuSizeByKey(sizeKey) {
      const entry =
        DANMAKU_SIZES.find((item) => item.key === sizeKey) ||
        DANMAKU_SIZES.find((item) => item.key === DEFAULT_DANMAKU_SIZE_KEY) ||
        DANMAKU_SIZES[0];
      storageSet(DANMAKU_SIZE_STORAGE_KEY, entry.key);
      window.dm_scale = entry.scale;
      applyDanmakuScaleToInstance(window.dm);
      syncDanmakuSizeMenu();
    }

    function buildDanmakuSizeSubView() {
      const view = document.createElement("div");
      view.dataset.menuView = "danmaku-size";
      view.className = "flex flex-col gap-0.5";
      view.hidden = true;
      view.appendChild(makeSubmenuHeader(t("Danmaku size"), "main"));
      for (const entry of DANMAKU_SIZES) {
        const btn = makeOptionButton(
          getDanmakuSizeLabel(entry.key),
          { danmakuSize: entry.key },
          () => {
            setDanmakuSizeByKey(entry.key);
            closeSettings();
          }
        );
        view.appendChild(btn);
      }
      return view;
    }

    function setupVodMenu(options) {
      if (!settingsList) return;
      const sources = Array.isArray(options?.sources)
        ? options.sources
        : window.supported_src || [];
      const isDash = Boolean(options?.isDash ?? window.is_dash);
      const audioOnly = Boolean(options?.isAudio ?? isAudio);
      const onSelectVideo = options?.onSelectVideo;
      const sorted = [...sources].sort((a, b) => b.quality - a.quality);
      const currentQn =
        !isDash && video.src
          ? new URL(video.src, document.baseURI).pathname.match(/_(\d+)(?:\.[^./]+)?$/)?.[1]
          : null;

      settingsList.innerHTML = "";
      const main = document.createElement("div");
      main.dataset.menuView = "main";
      main.className = "flex flex-col gap-0.5";
      if (!audioOnly) {
        main.appendChild(
          makeMenuNavRow("resolution", t("Resolution"), "data-resolution-value", "video")
        );
      }
      if (isDash) {
        main.appendChild(makeMenuNavRow("audio", t("Audio"), "data-audio-value", "audio"));
      }
      main.appendChild(
        makeMenuNavRow("speed", t("Playback speed"), "data-speed-value", "speed", syncSpeedMenu)
      );
      if (!audioOnly) {
        main.appendChild(
          makeMenuNavRow(
            "danmaku",
            t("Danmaku size"),
            "data-danmaku-size-value",
            "danmaku-size",
            syncDanmakuSizeMenu
          )
        );
      }
      settingsList.appendChild(main);

      if (!audioOnly) {
        const videoView = document.createElement("div");
        videoView.dataset.menuView = "video";
        videoView.className = "flex flex-col gap-0.5";
        videoView.hidden = true;
        videoView.appendChild(makeSubmenuHeader(t("Resolution"), "main"));
        const codecRowWrap = document.createElement("div");
        codecRowWrap.dataset.videoCodecRow = "1";
        codecRowWrap.className = "hidden";
        codecRowWrap.appendChild(
          makeMenuNavRow("resolution", t("Codec"), "data-codec-value", "video-codec")
        );
        const codecSep = document.createElement("div");
        codecSep.className = "my-1 h-px bg-white/10";
        codecRowWrap.appendChild(codecSep);
        videoView.appendChild(codecRowWrap);
        const videoOpts = document.createElement("div");
        videoOpts.dataset.videoOptions = "1";
        videoOpts.className = "flex flex-col gap-0.5";
        const cleanQualityLabel = (text) =>
          String(text || "")
            .replace(
              /\s*(?:超高清|超清|高清|清晰|流畅|流暢|标清|標清|极速|極速|高码率|高碼率|高帧率|高幀率)/g,
              ""
            )
            .replace(/(\d+)P\b/g, "$1p")
            .trim();
        sorted.forEach((src, idx) => {
          const cleanLabel = cleanQualityLabel(src.new_description) || src.new_description;
          const btn = makeOptionButton(
            cleanLabel,
            { videoQn: src.quality, qn: src.quality },
            (clickedBtn) => {
              if (typeof onSelectVideo === "function") {
                onSelectVideo(src);
              }
              const rv = settingsList.querySelector("[data-resolution-value]");
              if (rv) rv.textContent = cleanLabel;
              settingsList.querySelectorAll("button[data-video-qn]").forEach((b) => {
                b.classList.remove("active");
                b.setAttribute("aria-checked", "false");
              });
              clickedBtn.classList.add("active");
              clickedBtn.setAttribute("aria-checked", "true");
              closeSettings();
            }
          );
          const matchesCurrent = isDash ? idx === 0 : currentQn === String(src.quality);
          if (matchesCurrent) {
            btn.classList.add("active");
            btn.setAttribute("aria-checked", "true");
          }
          videoOpts.appendChild(btn);
        });
        videoView.appendChild(videoOpts);
        settingsList.appendChild(videoView);

        if (isDash) {
          const videoCodecView = document.createElement("div");
          videoCodecView.dataset.menuView = "video-codec";
          videoCodecView.className = "flex flex-col gap-0.5";
          videoCodecView.hidden = true;
          videoCodecView.appendChild(makeSubmenuHeader(t("Codec"), "video"));
          const codecOpts = document.createElement("div");
          codecOpts.dataset.videoCodecOptions = "1";
          codecOpts.className = "flex flex-col gap-0.5";
          videoCodecView.appendChild(codecOpts);
          settingsList.appendChild(videoCodecView);
        }
      }

      if (isDash) {
        const audioView = document.createElement("div");
        audioView.dataset.menuView = "audio";
        audioView.className = "flex flex-col gap-0.5";
        audioView.hidden = true;
        audioView.appendChild(makeSubmenuHeader(t("Audio"), "main"));
        const audioOpts = document.createElement("div");
        audioOpts.dataset.audioOptions = "1";
        audioOpts.className = "flex flex-col gap-0.5";
        audioView.appendChild(audioOpts);
        settingsList.appendChild(audioView);
      }

      settingsList.appendChild(buildSpeedSubView());
      if (!audioOnly) {
        settingsList.appendChild(buildDanmakuSizeSubView());
        syncDanmakuSizeMenu();
      }
      syncSpeedMenu();

      if (!audioOnly) {
        const activeVideoBtn =
          settingsList.querySelector("button[data-video-qn].active") ||
          settingsList.querySelector("button[data-video-qn]");
        if (activeVideoBtn) {
          activeVideoBtn.classList.add("active");
          activeVideoBtn.setAttribute("aria-checked", "true");
          const rv = settingsList.querySelector("[data-resolution-value]");
          const span = activeVideoBtn.querySelector("span");
          if (rv && span) rv.textContent = span.textContent;
        }
      }
    }

    function setAudioTracks(tracks, currentId, onSelectAudio) {
      if (!settingsList) return;
      const optsContainer = settingsList.querySelector("[data-audio-options]");
      if (!optsContainer) return;
      optsContainer.innerHTML = "";
      for (const trk of tracks || []) {
        const btn = makeOptionButton(trk.label, { audioQn: trk.id }, () => {
          if (typeof onSelectAudio === "function") onSelectAudio(trk.id);
          syncAudioTrack(trk.id);
          closeSettings();
        });
        optsContainer.appendChild(btn);
      }
      syncAudioTrack(currentId);
    }

    function syncAudioTrack(currentId) {
      if (!settingsList) return;
      settingsList.querySelectorAll("button[data-audio-qn]").forEach((b) => {
        const active = currentId != null && String(b.dataset.audioQn) === String(currentId);
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      if (currentId != null) {
        const activeBtn = settingsList.querySelector(`button[data-audio-qn="${currentId}"]`);
        const av = settingsList.querySelector("[data-audio-value]");
        if (activeBtn && av) {
          const span = activeBtn.querySelector("span");
          if (span) av.textContent = span.textContent;
        }
      }
    }

    function setVideoCodecs(codecs, currentId, onSelectCodec) {
      if (!settingsList) return;
      const rowWrap = settingsList.querySelector("[data-video-codec-row]");
      const optsContainer = settingsList.querySelector("[data-video-codec-options]");
      if (!optsContainer || !rowWrap) return;
      const list = Array.isArray(codecs) ? codecs : [];
      rowWrap.classList.toggle("hidden", list.length <= 1);
      optsContainer.innerHTML = "";
      for (const item of list) {
        const btn = makeOptionButton(item.label, { videoCodecId: item.id }, () => {
          if (typeof onSelectCodec === "function") onSelectCodec(item.id);
          syncVideoCodec(item.id);
          closeSettings();
        });
        optsContainer.appendChild(btn);
      }
      syncVideoCodec(currentId);
    }

    function syncVideoCodec(currentId) {
      if (!settingsList) return;
      settingsList.querySelectorAll("button[data-video-codec-id]").forEach((b) => {
        const active = currentId != null && String(b.dataset.videoCodecId) === String(currentId);
        b.classList.toggle("active", active);
        b.setAttribute("aria-checked", active ? "true" : "false");
      });
      if (currentId != null) {
        const activeBtn = settingsList.querySelector(`button[data-video-codec-id="${currentId}"]`);
        const cv = settingsList.querySelector("[data-codec-value]");
        if (activeBtn && cv) {
          const span = activeBtn.querySelector("span");
          if (span) cv.textContent = span.textContent;
        }
      }
    }

    function setVideoTracks(tracks, currentId, onSelectVideo) {
      if (!settingsList) return;
      const optsContainer = settingsList.querySelector("[data-video-options]");
      if (!optsContainer) return;
      optsContainer.innerHTML = "";
      let activeTrk = null;
      for (const trk of tracks || []) {
        if (currentId != null && String(trk.id) === String(currentId)) activeTrk = trk;
        const btn = makeOptionButton(
          trk.label,
          { videoQn: trk.qn, qn: trk.qn, videoRepId: trk.id },
          () => {
            if (typeof onSelectVideo === "function") onSelectVideo(trk);
            syncVideoQuality(trk.qn, trk.label, trk.id);
            closeSettings();
          }
        );
        optsContainer.appendChild(btn);
      }
      if (!activeTrk && tracks && tracks.length) activeTrk = tracks[0];
      if (activeTrk) {
        syncVideoQuality(activeTrk.qn, activeTrk.label, activeTrk.id);
      }
    }

    function syncVideoQuality(qn, labelText, repId) {
      if (!settingsList || (!qn && !repId)) return;
      const btn =
        (repId != null && settingsList.querySelector(`button[data-video-rep-id="${repId}"]`)) ||
        settingsList.querySelector(`button[data-qn="${qn}"]`);
      if (btn) {
        settingsList.querySelectorAll("button[data-video-qn]").forEach((b) => {
          b.classList.remove("active");
          b.setAttribute("aria-checked", "false");
        });
        btn.classList.add("active");
        btn.setAttribute("aria-checked", "true");
      }
      const rv = settingsList.querySelector("[data-resolution-value]");
      if (rv) {
        const text = labelText || btn?.querySelector("span")?.textContent || "";
        if (text) rv.textContent = text;
      }
    }

    function setupLiveMenu(options) {
      if (!settingsList) return;
      const items = Array.isArray(options?.items) ? options.items : [];
      const onSelect = options?.onSelect;

      settingsList.innerHTML = "";
      const main = document.createElement("div");
      main.dataset.menuView = "main";
      main.className = "flex flex-col gap-0.5";
      if (items.length > 0) {
        main.appendChild(
          makeMenuNavRow("resolution", t("Resolution"), "data-resolution-value", "video")
        );
      }
      main.appendChild(
        makeMenuNavRow(
          "danmaku",
          t("Danmaku size"),
          "data-danmaku-size-value",
          "danmaku-size",
          syncDanmakuSizeMenu
        )
      );
      settingsList.appendChild(main);

      if (items.length > 0) {
        const videoView = document.createElement("div");
        videoView.dataset.menuView = "video";
        videoView.className = "flex flex-col gap-0.5";
        videoView.hidden = true;
        videoView.appendChild(makeSubmenuHeader(t("Resolution"), "main"));

        items.forEach((item) => {
          const btn = makeOptionButton(item.label, { liveQn: item.value }, (clickedBtn) => {
            if (typeof onSelect === "function") onSelect(item);
            const rv = settingsList.querySelector("[data-resolution-value]");
            if (rv) rv.textContent = item.label;
            settingsList.querySelectorAll("button[data-live-qn]").forEach((b) => {
              b.classList.remove("active");
              b.setAttribute("aria-checked", "false");
            });
            clickedBtn.classList.add("active");
            clickedBtn.setAttribute("aria-checked", "true");
            closeSettings();
          });
          if (item.active) {
            btn.classList.add("active");
            btn.setAttribute("aria-checked", "true");
          }
          videoView.appendChild(btn);
        });
        settingsList.appendChild(videoView);
      }
      settingsList.appendChild(buildDanmakuSizeSubView());
      syncDanmakuSizeMenu();

      const activeBtn =
        settingsList.querySelector("button[data-live-qn].active") ||
        settingsList.querySelector("button[data-live-qn]");
      if (activeBtn) {
        activeBtn.classList.add("active");
        activeBtn.setAttribute("aria-checked", "true");
        const rv = settingsList.querySelector("[data-resolution-value]");
        const span = activeBtn.querySelector("span");
        if (rv && span) rv.textContent = span.textContent;
      }
    }

    function syncLiveQuality(labelText) {
      if (!settingsList || !labelText) return;
      const rv = settingsList.querySelector("[data-resolution-value]");
      if (rv) rv.textContent = labelText;
    }

    function openSettings() {
      if (!settingsMenu || !settingsBtn) return;
      menuInvoker = null;
      showControls(false);
      syncSpeedMenu();
      syncDanmakuSizeMenu();
      const brect = settingsBtn.getBoundingClientRect();
      const crect = root.getBoundingClientRect();
      settingsMenu.style.right = Math.max(8, crect.right - brect.right) + "px";
      const bottom = Math.max(48, crect.bottom - brect.top + 10);
      settingsMenu.style.bottom = bottom + "px";
      if (settingsList) {
        const { paddingTop, paddingBottom } = window.getComputedStyle(settingsMenu);
        const available =
          crect.height - bottom - parseFloat(paddingTop) - parseFloat(paddingBottom);
        settingsList.style.maxHeight = clamp(available, 0, 320) + "px";
      }
      settingsMenu.classList.remove("opacity-0", "pointer-events-none", "scale-95");
      settingsMenu.classList.add("opacity-100", "scale-100", "pointer-events-auto");
      settingsMenu.inert = false;
      settingsBtn.setAttribute("aria-expanded", "true");
      settingsOpen = true;
      switchMenuView("main");
    }

    function closeSettings() {
      if (!settingsMenu || !settingsBtn) return;
      settingsMenu.classList.add("opacity-0", "pointer-events-none", "scale-95");
      settingsMenu.classList.remove("opacity-100", "scale-100", "pointer-events-auto");
      if (
        typeof settingsMenu.contains === "function" &&
        settingsMenu.contains(document.activeElement)
      ) {
        settingsBtn.focus?.();
      }
      settingsMenu.inert = true;
      settingsBtn.setAttribute("aria-expanded", "false");
      settingsOpen = false;
      scheduleIdle();
    }

    function toggleSettings() {
      if (settingsOpen) closeSettings();
      else openSettings();
    }

    function setDanmakuActive(enabled) {
      if (!danmakuBtn) return;
      danmakuBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
      danmakuBtn.style.opacity = enabled ? "1" : "0.5";
    }

    function syncFullscreenState() {
      const isFs = Boolean(
        document.fullscreenElement === root || document.webkitFullscreenElement === root
      );
      if (iconFsEnter) iconFsEnter.classList.toggle("hidden", isFs);
      if (iconFsExit) iconFsExit.classList.toggle("hidden", !isFs);
      setBtnLabel(fullscreenBtn, isFs ? t("Exit fullscreen") : t("Fullscreen"));
      if (window.dm && typeof window.dm.resize === "function") {
        setTimeout(() => window.dm && window.dm.resize(), 120);
      }
    }

    function toggleFullscreen() {
      if (document.fullscreenElement === root || document.webkitFullscreenElement === root) {
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (exit) Promise.resolve(exit.call(document)).catch(() => {});
        return;
      }
      if (typeof root.requestFullscreen === "function") {
        root.requestFullscreen().catch(() => {
          if (typeof video.webkitEnterFullscreen === "function") {
            video.webkitEnterFullscreen();
          }
        });
      } else if (typeof root.webkitRequestFullscreen === "function") {
        root.webkitRequestFullscreen();
      } else if (typeof video.webkitEnterFullscreen === "function") {
        video.webkitEnterFullscreen();
      }
    }

    function syncPipState() {
      if (!pipBtn) return;
      const supported = Boolean(document.pictureInPictureEnabled && !video.disablePictureInPicture);
      pipBtn.classList.toggle("hidden", !supported);
      const active = document.pictureInPictureElement === video;
      setBtnLabel(pipBtn, active ? t("Exit Picture-in-Picture") : t("Picture-in-Picture"));
      pipBtn.setAttribute("aria-pressed", active ? "true" : "false");
    }

    function togglePip() {
      if (!document.pictureInPictureEnabled) return;
      if (document.pictureInPictureElement === video) {
        document.exitPictureInPicture().catch(() => {});
      } else if (typeof video.requestPictureInPicture === "function") {
        video.requestPictureInPicture().catch(() => {});
      }
    }

    function setTheater(enabled, persist) {
      const layout = document.getElementById("video-layout");
      if (!layout) return;
      if (enabled && document.pictureInPictureElement === video) {
        document.exitPictureInPicture().catch(() => {});
      }
      if (enabled) {
        layout.setAttribute("data-theater", "true");
      } else {
        layout.removeAttribute("data-theater");
      }
      if (theaterBtn) {
        theaterBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
        setBtnLabel(theaterBtn, enabled ? t("Exit theater mode") : t("Theater mode"));
      }
      if (iconTheaterEnter) iconTheaterEnter.classList.toggle("hidden", enabled);
      if (iconTheaterExit) iconTheaterExit.classList.toggle("hidden", !enabled);
      if (persist) storageSet(THEATER_STORAGE_KEY, enabled ? "1" : "0");
      if (window.dm && typeof window.dm.resize === "function") {
        setTimeout(() => window.dm && window.dm.resize(), 120);
      }
    }

    function toggleTheater() {
      const layout = document.getElementById("video-layout");
      const current = Boolean(layout && layout.getAttribute("data-theater") === "true");
      setTheater(!current, true);
    }

    if (!isAudio && storageGet(THEATER_STORAGE_KEY) === "1") {
      setTheater(true, false);
    }

    if (playBtn) {
      playBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          togglePlay(false);
        },
        { signal }
      );
    }

    if (muteBtn) {
      muteBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          toggleMute(false);
        },
        { signal }
      );
    }

    if (timeToggleBtns && timeToggleBtns.length > 0 && !isLive) {
      timeToggleBtns.forEach((btn) => {
        btn.addEventListener(
          "click",
          (e) => {
            e.stopPropagation();
            toggleTimeDisplayMode();
          },
          { signal }
        );
      });
    }

    if (volumeGroup) {
      volumeGroup.addEventListener(
        "wheel",
        (e) => {
          e.preventDefault();
          const step = Math.min(0.05, (Math.abs(e.deltaY) / 100) * 0.05) || 0.05;
          const delta = e.deltaY < 0 ? step : -step;
          const base = video.muted ? 0 : video.volume;
          setVolume(base + delta, true);
        },
        { passive: false, signal }
      );
    }

    if (volumeSlider) {
      const applyVolPointer = (e) => {
        setVolume(ratioFromPointer(volumeSlider, e.clientX), false);
      };
      volumeSlider.addEventListener(
        "pointerdown",
        (e) => {
          e.stopPropagation();
          if (volumePointerId !== null) return;
          volumePointerId = e.pointerId;
          if (typeof volumeSlider.setPointerCapture === "function") {
            volumeSlider.setPointerCapture(e.pointerId);
          }
          applyVolPointer(e);
          showControls(false);
        },
        { signal }
      );
      volumeSlider.addEventListener(
        "pointermove",
        (e) => {
          if (volumePointerId !== e.pointerId) return;
          applyVolPointer(e);
        },
        { signal }
      );
      const endVolDrag = (e) => {
        if (volumePointerId !== e.pointerId) return;
        volumePointerId = null;
        if (
          typeof volumeSlider.releasePointerCapture === "function" &&
          volumeSlider.hasPointerCapture?.(e.pointerId)
        ) {
          volumeSlider.releasePointerCapture(e.pointerId);
        }
        scheduleIdle();
      };
      volumeSlider.addEventListener("pointerup", endVolDrag, { signal });
      volumeSlider.addEventListener("pointercancel", endVolDrag, { signal });
      volumeSlider.addEventListener(
        "keydown",
        (e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowRight") {
            e.preventDefault();
            setVolume((video.muted ? 0 : video.volume) + 0.05, true);
          } else if (e.key === "ArrowDown" || e.key === "ArrowLeft") {
            e.preventDefault();
            setVolume((video.muted ? 0 : video.volume) - 0.05, true);
          }
        },
        { signal }
      );
    }

    if (scrubber && !isLive) {
      scrubber.addEventListener(
        "pointerenter",
        (e) => {
          if (e.pointerType !== "mouse") return;
          scrubber.setAttribute("data-pointing", "");
          updateScrubberTooltip(ratioFromPointer(scrubber, e.clientX));
        },
        { signal }
      );

      scrubber.addEventListener(
        "pointerleave",
        () => {
          if (scrubPointerId === null) {
            scrubber.removeAttribute("data-pointing");
          }
        },
        { signal }
      );

      scrubber.addEventListener(
        "pointerdown",
        (e) => {
          e.stopPropagation();
          if (scrubPointerId !== null) return;
          scrubPointerId = e.pointerId;
          scrubber.setAttribute("data-dragging", "");
          scrubber.setAttribute("data-pointing", "");
          if (typeof scrubber.setPointerCapture === "function") {
            scrubber.setPointerCapture(e.pointerId);
          }
          const ratio = ratioFromPointer(scrubber, e.clientX);
          const dur = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
          pendingSeekTime = ratio * dur;
          updateScrubberVisual(pendingSeekTime, dur);
          updateScrubberTooltip(ratio);
          showControls(false);
        },
        { signal }
      );

      scrubber.addEventListener(
        "pointermove",
        (e) => {
          if (scrubPointerId !== null && scrubPointerId !== e.pointerId) return;
          const ratio = ratioFromPointer(scrubber, e.clientX);
          updateScrubberTooltip(ratio);
          if (scrubPointerId === null) return;
          const dur = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
          pendingSeekTime = ratio * dur;
          updateScrubberVisual(pendingSeekTime, dur);
        },
        { signal }
      );

      const finishScrub = (e, commit) => {
        if (scrubPointerId !== e.pointerId) return;
        scrubPointerId = null;
        scrubber.removeAttribute("data-dragging");
        if (e.pointerType !== "mouse") {
          scrubber.removeAttribute("data-pointing");
        }
        if (
          typeof scrubber.releasePointerCapture === "function" &&
          scrubber.hasPointerCapture?.(e.pointerId)
        ) {
          scrubber.releasePointerCapture(e.pointerId);
        }
        if (commit && pendingSeekTime !== null && Number.isFinite(pendingSeekTime)) {
          const target = pendingSeekTime;
          pendingSeekTime = null;
          video.currentTime = target;
        } else {
          pendingSeekTime = null;
          updateScrubberVisual(video.currentTime, video.duration);
        }
        scheduleIdle();
      };

      scrubber.addEventListener("pointerup", (e) => finishScrub(e, true), { signal });
      scrubber.addEventListener("pointercancel", (e) => finishScrub(e, false), { signal });
      scrubber.addEventListener(
        "keydown",
        (e) => {
          if (!canSeek()) return;
          if (e.key === "ArrowLeft") {
            e.preventDefault();
            seekBy(-5, true);
          } else if (e.key === "ArrowRight") {
            e.preventDefault();
            seekBy(5, true);
          }
        },
        { signal }
      );
    }

    if (liveBtn && isLive) {
      liveBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          if (video.buffered && video.buffered.length > 0) {
            const edge = video.buffered.end(video.buffered.length - 1);
            if (Number.isFinite(edge) && edge > 0) {
              video.currentTime = Math.max(0, edge - 0.5);
            }
          }
          if (video.paused) {
            video.play().catch(() => {});
          }
        },
        { signal }
      );
    }

    if (captionsSelect) {
      captionsSelect.addEventListener(
        "change",
        () => enforceSingleSubtitle(renderedSubtitles[captionsSelect.selectedIndex - 1] || null),
        { signal }
      );
    }

    if (settingsBtn && settingsMenu) {
      settingsBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          toggleSettings();
        },
        { signal }
      );
      settingsMenu.addEventListener(
        "keydown",
        (e) => {
          if (!settingsOpen || e.altKey || e.ctrlKey || e.metaKey) return;
          const items = visibleMenuItems();
          if (!items.length) return;
          const index = items.indexOf(document.activeElement);
          let next;
          if (e.key === "ArrowDown") next = (index + 1) % items.length;
          else if (e.key === "ArrowUp") next = (index <= 0 ? items.length : index) - 1;
          else if (e.key === "Home") next = 0;
          else if (e.key === "End") next = items.length - 1;
          else return;
          e.preventDefault();
          items[next].focus();
        },
        { signal }
      );
      document.addEventListener(
        "click",
        (e) => {
          if (!settingsOpen) return;
          if (!settingsBtn.contains(e.target) && !settingsMenu.contains(e.target)) {
            closeSettings();
          }
        },
        { signal }
      );
    }

    if (pipBtn) {
      pipBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          togglePip();
        },
        { signal }
      );
    }

    if (theaterBtn) {
      theaterBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          toggleTheater();
        },
        { signal }
      );
    }

    if (fullscreenBtn) {
      fullscreenBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          toggleFullscreen();
        },
        { signal }
      );
    }

    if (bottomBar) {
      bottomBar.addEventListener(
        "pointerenter",
        (e) => {
          if (e.pointerType !== "mouse") return;
          pointerInBar = true;
          showControls(false);
        },
        { signal }
      );
      bottomBar.addEventListener(
        "pointerleave",
        (e) => {
          if (e.pointerType !== "mouse") return;
          pointerInBar = false;
          scheduleIdle();
        },
        { signal }
      );
    }

    root.addEventListener(
      "focus",
      () => {
        if (typeof root.matches === "function" && root.matches(":focus-visible")) {
          showControls(false);
        }
      },
      { signal }
    );
    root.addEventListener(
      "focusout",
      (e) => {
        if (e.target === root) scheduleIdle();
      },
      { signal }
    );

    root.addEventListener(
      "pointermove",
      (e) => {
        if (e.pointerType !== "mouse") return;
        showControls(true);
      },
      { signal }
    );

    root.addEventListener(
      "pointerleave",
      (e) => {
        if (e.pointerType !== "mouse") return;
        pointerInBar = false;
        hideControls();
      },
      { signal }
    );

    if (gestureLayer) {
      gestureLayer.addEventListener(
        "pointerup",
        (e) => {
          if (settingsOpen) {
            closeSettings();
            return;
          }
          if (e.pointerType === "touch") {
            const rect = gestureLayer.getBoundingClientRect();
            const relX = rect.width ? (e.clientX - rect.left) / rect.width : 0.5;
            const region = relX < 0.3 ? "left" : relX > 0.7 ? "right" : "center";
            const now = performance.now();
            if (now - lastTapAt <= TOUCH_DOUBLE_TAP_MS && lastTapRegion === region) {
              if (clickTimer) {
                clearTimeout(clickTimer);
                clickTimer = null;
              }
              lastTapAt = 0;
              lastTapRegion = null;
              if (region === "left" && !isLive) {
                seekBy(-10, true);
              } else if (region === "right" && !isLive) {
                seekBy(10, true);
              } else {
                togglePlay(true);
              }
              return;
            }
            lastTapAt = now;
            lastTapRegion = region;
            if (clickTimer) clearTimeout(clickTimer);
            clickTimer = setTimeout(() => {
              clickTimer = null;
              const visible = bottomBar && bottomBar.hasAttribute("data-visible");
              if (visible && !video.paused && !video.ended) {
                hideControls();
              } else {
                showControls(true);
              }
            }, TOUCH_DOUBLE_TAP_MS);
            return;
          }
          if (e.button !== 0) return;
          if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
            if (!isAudio) toggleFullscreen();
            else togglePlay(false);
          } else {
            clickTimer = setTimeout(() => {
              clickTimer = null;
              togglePlay(false);
            }, MOUSE_CLICK_DELAY_MS);
          }
        },
        { signal }
      );
    }

    document.addEventListener(
      "keydown",
      (e) => {
        if (e.key === "Escape" && settingsOpen && root.contains(e.target)) {
          e.preventDefault();
          closeSettings();
          return;
        }
        if (e.key === "Tab" && e.target === root) showControls(false);
        if (blocksPlayerShortcut(e, root)) return;
        const key = e.key;
        if (key === " " || key === "k" || key === "K") {
          e.preventDefault();
          togglePlay(true);
        } else if (key === "ArrowLeft") {
          if (!canSeek()) return;
          e.preventDefault();
          seekBy(-5, true);
        } else if (key === "ArrowRight") {
          if (!canSeek()) return;
          e.preventDefault();
          seekBy(5, true);
        } else if (key === "j" || key === "J") {
          if (!canSeek()) return;
          e.preventDefault();
          seekBy(-10, true);
        } else if (key === "l" || key === "L") {
          if (!canSeek()) return;
          e.preventDefault();
          seekBy(10, true);
        } else if (key === "ArrowUp") {
          e.preventDefault();
          setVolume((video.muted ? 0 : video.volume) + 0.05, true);
        } else if (key === "ArrowDown") {
          e.preventDefault();
          setVolume((video.muted ? 0 : video.volume) - 0.05, true);
        } else if (key === "m" || key === "M") {
          e.preventDefault();
          toggleMute(true);
        } else if ((key === "f" || key === "F") && !isAudio) {
          const fullscreen =
            document.fullscreenElement === root || document.webkitFullscreenElement === root;
          if (
            fullscreen
              ? !(document.exitFullscreen || document.webkitExitFullscreen)
              : !(
                  root.requestFullscreen ||
                  root.webkitRequestFullscreen ||
                  video.webkitEnterFullscreen
                )
          )
            return;
          e.preventDefault();
          toggleFullscreen();
        } else if ((key === "t" || key === "T") && !isAudio) {
          if (!document.getElementById("video-layout")) return;
          e.preventDefault();
          toggleTheater();
        } else if ((key === "i" || key === "I") && !isAudio) {
          if (
            !document.pictureInPictureEnabled ||
            (document.pictureInPictureElement === video
              ? typeof document.exitPictureInPicture !== "function"
              : video.disablePictureInPicture ||
                typeof video.requestPictureInPicture !== "function")
          )
            return;
          e.preventDefault();
          togglePip();
        } else if (key === "c" || key === "C") {
          if (!isLive && renderedSubtitles.length) {
            e.preventDefault();
            toggleCaptions();
          }
        } else if ((key === "d" || key === "D") && !isAudio) {
          if (window.dm && typeof window.toggleDanmaku === "function") {
            e.preventDefault();
            window.toggleDanmaku();
            showHud("danmaku", window.dm_status ? t("On") : t("Off"));
          }
        } else if (key >= "0" && key <= "9") {
          if (canSeek()) {
            e.preventDefault();
            const target = (Number(key) / 10) * video.duration;
            video.currentTime = target;
            updateScrubberVisual(target, video.duration);
            showControls(true);
          }
        }
      },
      { signal }
    );

    video.addEventListener("play", syncPlayState, { signal });
    video.addEventListener(
      "pause",
      () => {
        clearStallTimer();
        setSpinnerVisible(false);
        syncPlayState();
      },
      { signal }
    );
    video.addEventListener("ended", syncPlayState, { signal });
    video.addEventListener("volumechange", syncVolumeState, { signal });
    video.addEventListener("ratechange", syncSpeedMenu, { signal });
    video.addEventListener(
      "loadedmetadata",
      () => {
        updateScrubberVisual(video.currentTime, video.duration);
        syncBufferBar();
        enforceSingleSubtitle();
      },
      { signal }
    );
    video.addEventListener(
      "durationchange",
      () => {
        if (scrubPointerId === null) updateScrubberVisual(video.currentTime, video.duration);
        syncBufferBar();
      },
      { signal }
    );
    video.addEventListener(
      "timeupdate",
      () => {
        if (scrubPointerId === null) {
          updateScrubberVisual(video.currentTime, video.duration);
        }
        syncBufferBar();
      },
      { signal }
    );
    video.addEventListener("progress", syncBufferBar, { signal });
    video.addEventListener(
      "loadstart",
      () => {
        clearStallTimer();
        if (!hasPlayedOnce) setSpinnerVisible(true);
      },
      { signal }
    );
    video.addEventListener(
      "waiting",
      () => {
        clearStallTimer();
        if (video.paused) return;
        if (!hasPlayedOnce) {
          setSpinnerVisible(true);
        } else {
          stallTimer = setTimeout(() => {
            stallTimer = null;
            setSpinnerVisible(true);
          }, STALL_DELAY_MS);
        }
      },
      { signal }
    );
    const clearLoading = () => {
      hasPlayedOnce = true;
      clearStallTimer();
      setSpinnerVisible(false);
    };
    video.addEventListener("playing", clearLoading, { signal });
    video.addEventListener(
      "canplay",
      () => {
        clearStallTimer();
        setSpinnerVisible(false);
      },
      { signal }
    );
    video.addEventListener(
      "error",
      () => {
        clearStallTimer();
        setSpinnerVisible(false);
      },
      { signal }
    );

    if (video.textTracks && typeof video.textTracks.addEventListener === "function") {
      video.textTracks.addEventListener("change", () => enforceSingleSubtitle(), { signal });
      video.textTracks.addEventListener("addtrack", () => enforceSingleSubtitle(), { signal });
    }

    document.addEventListener("fullscreenchange", syncFullscreenState, { signal });
    document.addEventListener("webkitfullscreenchange", syncFullscreenState, { signal });
    video.addEventListener("enterpictureinpicture", syncPipState, { signal });
    video.addEventListener("leavepictureinpicture", syncPipState, { signal });

    syncPlayState();
    syncVolumeState();
    syncFullscreenState();
    syncPipState();
    syncCaptionsState();
    setDanmakuActive(window.dm_status !== false);
    if (!isLive) {
      updateScrubberVisual(video.currentTime, video.duration);
      syncBufferBar();
    }

    return {
      destroy() {
        ac.abort();
        if (idleTimer) clearTimeout(idleTimer);
        if (stallTimer) clearTimeout(stallTimer);
        if (hudTimer) clearTimeout(hudTimer);
        if (clickTimer) clearTimeout(clickTimer);
        idleTimer = null;
        stallTimer = null;
        hudTimer = null;
        clickTimer = null;
      },
      openSettings,
      closeSettings,
      toggleSettings,
      setDanmakuActive,
      showHud,
      setupVodMenu,
      setVideoCodecs,
      syncVideoCodec,
      setVideoTracks,
      setAudioTracks,
      syncAudioTrack,
      syncVideoQuality,
      setupLiveMenu,
      syncLiveQuality,
    };
  }

  window.MikuPlayerUI = {
    mount(rootEl, videoEl, opts) {
      if (activeInstance) {
        activeInstance.destroy();
        activeInstance = null;
      }
      if (!rootEl || !videoEl) return null;
      activeInstance = createController(rootEl, videoEl, opts);
      return activeInstance;
    },
    destroy() {
      if (activeInstance) {
        activeInstance.destroy();
        activeInstance = null;
      }
    },
    openSettings() {
      activeInstance?.openSettings();
    },
    closeSettings() {
      activeInstance?.closeSettings();
    },
    toggleSettings() {
      activeInstance?.toggleSettings();
    },
    setDanmakuActive(enabled) {
      activeInstance?.setDanmakuActive(enabled);
    },
    showHud(iconName, text) {
      activeInstance?.showHud(iconName, text);
    },
    setupVodMenu(options) {
      activeInstance?.setupVodMenu(options);
    },
    setVideoCodecs(codecs, currentId, onSelectCodec) {
      activeInstance?.setVideoCodecs(codecs, currentId, onSelectCodec);
    },
    syncVideoCodec(currentId) {
      activeInstance?.syncVideoCodec(currentId);
    },
    setVideoTracks(tracks, currentId, onSelectVideo) {
      activeInstance?.setVideoTracks(tracks, currentId, onSelectVideo);
    },
    setAudioTracks(tracks, currentId, onSelectAudio) {
      activeInstance?.setAudioTracks(tracks, currentId, onSelectAudio);
    },
    syncAudioTrack(currentId) {
      activeInstance?.syncAudioTrack(currentId);
    },
    syncVideoQuality(qn, labelText, repId) {
      activeInstance?.syncVideoQuality(qn, labelText, repId);
    },
    setupLiveMenu(options) {
      activeInstance?.setupLiveMenu(options);
    },
    syncLiveQuality(labelText) {
      activeInstance?.syncLiveQuality(labelText);
    },
    applyDanmakuScale(dm) {
      return applyDanmakuScaleToInstance(dm);
    },
    getDanmakuScale,
  };
})();
