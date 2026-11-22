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

  function t(key) {
    if (typeof window.I18n !== "undefined" && typeof window.I18n.t === "function") {
      return window.I18n.t(key);
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

  function blocksPlayerShortcut(event, root) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return true;
    const target = event.target;
    if (!target || !(target instanceof Element)) return false;
    if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) {
      return true;
    }
    if (target.closest("[role='menu'], [role='menuitem'], [data-volume-slider]")) {
      return true;
    }
    const interactive = target.closest("a, button, [role='button'], [role='slider'], summary");
    if (interactive && !root.contains(interactive)) {
      return true;
    }
    return false;
  }

  let activeInstance = null;

  function createController(root, video, opts) {
    const isLive = Boolean(opts && opts.isLive);
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
    const scrubber = root.querySelector("[data-scrubber]");
    const scrubberBuffer = root.querySelector("[data-scrubber-buffer]");
    const scrubberFill = root.querySelector("[data-scrubber-fill]");
    const scrubberThumb = root.querySelector("[data-scrubber-thumb]");
    const scrubberTooltip = root.querySelector("[data-scrubber-tooltip]");
    const danmakuBtn = root.querySelector("#danmaku-toggle");
    const captionsBtn = root.querySelector("[data-captions-btn]");
    const settingsBtn = root.querySelector("#quality-btn");
    const settingsMenu = root.querySelector("#quality-menu");
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
    let isScrubbing = false;
    let pendingSeekTime = null;
    let isVolumeDragging = false;
    let hasPlayedOnce = false;
    let settingsOpen = false;
    let lastSeekAt = 0;
    let seekAccum = 0;
    let savedVolume = 1;

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
      if (
        video.paused ||
        video.ended ||
        settingsOpen ||
        isScrubbing ||
        isVolumeDragging ||
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
        isScrubbing ||
        isVolumeDragging ||
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
      const durStr = formatTime(safeDur);
      if (timeCurrent) timeCurrent.textContent = curStr;
      if (timeCurrentCompact) timeCurrentCompact.textContent = curStr;
      if (timeDuration) timeDuration.textContent = durStr;
      if (timeDurationCompact) timeDurationCompact.textContent = durStr;
      if (scrubber) {
        scrubber.setAttribute("aria-valuemax", String(Math.floor(safeDur)));
        scrubber.setAttribute("aria-valuenow", String(Math.floor(safeTime)));
        scrubber.setAttribute("aria-valuetext", curStr + " / " + durStr);
      }
    }

    function syncBufferBar() {
      if (!scrubberBuffer || isLive) return;
      const dur = video.duration;
      if (!Number.isFinite(dur) || dur <= 0 || !video.buffered || video.buffered.length === 0) {
        scrubberBuffer.style.width = "0%";
        return;
      }
      const cur = video.currentTime;
      let end = 0;
      for (let i = 0; i < video.buffered.length; i++) {
        if (video.buffered.start(i) <= cur + 0.5 && video.buffered.end(i) > end) {
          end = video.buffered.end(i);
        }
      }
      if (end === 0) {
        end = video.buffered.end(video.buffered.length - 1);
      }
      scrubberBuffer.style.width = clamp((end / dur) * 100, 0, 100) + "%";
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

    function seekBy(delta, withHud) {
      if (isLive || !Number.isFinite(video.duration) || video.duration <= 0) return;
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
      const tracks = video.textTracks;
      if (!tracks || tracks.length === 0) return;
      let active = preferredTrack || null;
      for (let i = 0; i < tracks.length; i++) {
        const t = tracks[i];
        if (t.kind !== "subtitles" && t.kind !== "captions") continue;
        if (t.mode === "showing") {
          if (!active) {
            active = t;
          } else if (t !== active) {
            t.mode = "disabled";
          }
        }
      }
      syncCaptionsState();
    }

    function syncCaptionsState() {
      if (!captionsBtn) return;
      const tracks = video.textTracks;
      let showing = false;
      if (tracks) {
        for (let i = 0; i < tracks.length; i++) {
          const t = tracks[i];
          if ((t.kind === "subtitles" || t.kind === "captions") && t.mode === "showing") {
            showing = true;
            break;
          }
        }
      }
      captionsBtn.setAttribute("aria-pressed", showing ? "true" : "false");
    }

    function toggleCaptions(withHud) {
      const tracks = video.textTracks;
      if (!tracks || tracks.length === 0) return;
      const subTracks = [];
      for (let i = 0; i < tracks.length; i++) {
        if (tracks[i].kind === "subtitles" || tracks[i].kind === "captions") {
          subTracks.push(tracks[i]);
        }
      }
      if (subTracks.length === 0) return;
      const activeIdx = subTracks.findIndex((t) => t.mode === "showing");
      if (activeIdx >= 0) {
        subTracks.forEach((t) => {
          t.mode = "disabled";
        });
        syncCaptionsState();
        if (withHud) showHud("captions", t("Off"));
      } else {
        subTracks.forEach((t, idx) => {
          t.mode = idx === 0 ? "showing" : "disabled";
        });
        syncCaptionsState();
        if (withHud) showHud("captions", subTracks[0].label || t("On"));
      }
    }

    function openSettings() {
      if (!settingsMenu || !settingsBtn) return;
      const qlist = root.querySelector("#quality-list");
      if (qlist) {
        qlist.querySelectorAll("[data-menu-view]").forEach((v) => {
          v.hidden = v.dataset.menuView !== "main";
        });
      }
      const brect = settingsBtn.getBoundingClientRect();
      const crect = root.getBoundingClientRect();
      settingsMenu.style.right = Math.max(8, crect.right - brect.right) + "px";
      settingsMenu.style.bottom = Math.max(48, crect.bottom - brect.top + 10) + "px";
      settingsMenu.classList.remove("opacity-0", "pointer-events-none", "scale-95");
      settingsMenu.classList.add("opacity-100", "scale-100", "pointer-events-auto");
      settingsBtn.setAttribute("aria-expanded", "true");
      settingsOpen = true;
      showControls(false);
    }

    function closeSettings() {
      if (!settingsMenu || !settingsBtn) return;
      settingsMenu.classList.add("opacity-0", "pointer-events-none", "scale-95");
      settingsMenu.classList.remove("opacity-100", "scale-100", "pointer-events-auto");
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

    if (storageGet(THEATER_STORAGE_KEY) === "1") {
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
          isVolumeDragging = true;
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
          if (!isVolumeDragging) return;
          applyVolPointer(e);
        },
        { signal }
      );
      const endVolDrag = (e) => {
        if (!isVolumeDragging) return;
        isVolumeDragging = false;
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
          if (!isScrubbing) {
            scrubber.removeAttribute("data-pointing");
          }
        },
        { signal }
      );

      scrubber.addEventListener(
        "pointerdown",
        (e) => {
          e.stopPropagation();
          isScrubbing = true;
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
          const ratio = ratioFromPointer(scrubber, e.clientX);
          updateScrubberTooltip(ratio);
          if (!isScrubbing) return;
          const dur = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
          pendingSeekTime = ratio * dur;
          updateScrubberVisual(pendingSeekTime, dur);
        },
        { signal }
      );

      const finishScrub = (e, commit) => {
        if (!isScrubbing) return;
        isScrubbing = false;
        scrubber.removeAttribute("data-dragging");
        if (e && e.pointerType !== "mouse") {
          scrubber.removeAttribute("data-pointing");
        }
        if (
          e &&
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

    if (captionsBtn) {
      captionsBtn.addEventListener(
        "click",
        (e) => {
          e.stopPropagation();
          toggleCaptions(false);
        },
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
            toggleFullscreen();
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
        if (e.key === "Escape" && settingsOpen) {
          e.preventDefault();
          closeSettings();
          return;
        }
        if (blocksPlayerShortcut(e, root)) return;
        const key = e.key;
        if (key === " " || key === "k" || key === "K") {
          e.preventDefault();
          togglePlay(true);
        } else if (key === "ArrowLeft") {
          e.preventDefault();
          seekBy(-5, true);
        } else if (key === "ArrowRight") {
          e.preventDefault();
          seekBy(5, true);
        } else if (key === "j" || key === "J") {
          e.preventDefault();
          seekBy(-10, true);
        } else if (key === "l" || key === "L") {
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
        } else if (key === "f" || key === "F") {
          e.preventDefault();
          toggleFullscreen();
        } else if (key === "t" || key === "T") {
          e.preventDefault();
          toggleTheater();
        } else if (key === "i" || key === "I") {
          e.preventDefault();
          togglePip();
        } else if (key === "c" || key === "C") {
          if (!isLive && captionsBtn) {
            e.preventDefault();
            toggleCaptions(true);
          }
        } else if (key === "d" || key === "D") {
          if (typeof window.toggleDanmaku === "function") {
            e.preventDefault();
            window.toggleDanmaku();
            showHud("danmaku", window.dm_status ? t("On") : t("Off"));
          }
        } else if (!isLive && key >= "0" && key <= "9") {
          if (Number.isFinite(video.duration) && video.duration > 0) {
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
    video.addEventListener("pause", syncPlayState, { signal });
    video.addEventListener("ended", syncPlayState, { signal });
    video.addEventListener("volumechange", syncVolumeState, { signal });
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
        updateScrubberVisual(video.currentTime, video.duration);
        syncBufferBar();
      },
      { signal }
    );
    video.addEventListener(
      "timeupdate",
      () => {
        if (!isScrubbing) {
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
  };
})();
