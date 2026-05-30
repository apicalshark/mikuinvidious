/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

"use strict";

class LiveStreamManager {
  constructor(videoElement, streamUrl, qualityList, qualityLabel) {
    this.video = videoElement;
    this.url = streamUrl;
    this.qualityList = qualityList;
    this.qualityLabel = qualityLabel;
    this.player = null;
    this.isReconnecting = false;
    this.reconnectTimer = null;
    this.monitorInterval = null;
    this.hbInterval = null;
    this.speedZeroCount = 0;
    this.pauseTime = null;
    this.RECONNECT_THRESHOLD = 5; // Seconds
    this.destroyed = false;

    // CONFIGURATION CONSTANTS
    this.MAX_LATENCY_THRESHOLD = 8.0;
    this.NORMAL_SPEED = 1.0;

    // UNIQUE CLIENT ID FOR DISCONNECT PINGS
    this.clientId = Math.random().toString(36).substring(2, 15);
    if (this.url.includes("?")) {
      this.url += "&cid=" + this.clientId;
    } else {
      this.url += "?cid=" + this.clientId;
    }

    // Ensure absolute URL for Worker context
    this.url = new URL(this.url, window.location.href).href;

    // DISCONNECT PING ON UNLOAD
    this._pingHandler = () => {
      if (this.destroyed) return;
      // Extract room_id and vqn from URL: /proxy/live/ROOMID_VQN?cid=...
      const match = this.url.match(/\/proxy\/live\/([^_?]+)_?([^?]*)/);
      if (match) {
        const roomId = match[1];
        const vqn = match[2] || "default";
        const pingUrl = `/proxy/live/disconnect?room_id=${roomId}&vqn=${vqn}&cid=${this.clientId}`;
        navigator.sendBeacon(pingUrl);
      }
    };
    window.addEventListener("pagehide", this._pingHandler);
    window.addEventListener("beforeunload", this._pingHandler);
  }

  init() {
    if (!mpegts.isSupported() || this.destroyed) return;

    console.log("[LiveManager] Initializing stream:", this.url);
    this.initTime = Date.now();
    this.player = mpegts.createPlayer(
      {
        type: "flv",
        url: this.url,
        isLive: true,
      },
      {
        enableWorker: true,
        enableStashBuffer: true,
        stashInitialSize: 1024 * 384,
        fixAudioTimestampGap: true,
        autoCleanupSourceBuffer: true,
        autoCleanupMaxBackwardDuration: 30,
        autoCleanupMinBackwardDuration: 15,
        // BUILT-IN LATENCY MANAGEMENT (MPEGTS.JS OFFICIAL)
        isLive: true,
        liveSync: true,
        liveSyncMaxLatency: 5.0,
        liveSyncTargetLatency: 3.0,
        liveSyncPlaybackRate: 1.05,
        liveBufferLatencyChasing: true,
        liveBufferLatencyMaxLatency: 8.0,
        liveBufferLatencyMinRemain: 4.0,
      }
    );

    this.player.attachMediaElement(this.video);
    this.player.load();

    const playPromise = this.player.play();
    if (playPromise !== undefined) {
      playPromise.catch((error) => {
        if (error.name === "AbortError") return;
        console.error("[LiveManager] Play failed:", error);
        showAutoplayOverlay(this.video);
      });
    }

    this.startMonitoring();
    this.handleEvents();

    // Resume/Pause logic
    this.video.onpause = () => {
      this.pauseTime = Date.now();
    };
    this.video.onplay = () => {
      this.handleResume();
    };
  }

  handleResume() {
    if (!this.pauseTime) return;
    const duration = (Date.now() - this.pauseTime) / 1000;
    this.pauseTime = null;

    if (duration > this.RECONNECT_THRESHOLD) {
      console.log("[LiveManager] Long pause (" + duration.toFixed(2) + "s), reconnecting...");
      this.reconnect();
    } else {
      this.jumpToLiveEdge();
    }
  }

  jumpToLiveEdge() {
    if (this.video.buffered.length > 0) {
      const latest = this.video.buffered.end(this.video.buffered.length - 1);
      // Jump to 2 seconds before the edge to ensure smooth playback
      // but don't jump backwards if we are already ahead of that point
      const target = Math.max(this.video.currentTime, latest - 2.0);
      console.log(
        "[LiveManager] Short pause, jumping to target:",
        target.toFixed(2),
        "(buffer edge:",
        latest.toFixed(2),
        ")"
      );
      this.video.currentTime = target;
    }
  }

  startMonitoring() {
    if (this.monitorInterval) clearInterval(this.monitorInterval);
    this.monitorInterval = setInterval(() => {
      if (!this.player || !this.video.buffered.length) return;

      const bufferedEnd = this.video.buffered.end(this.video.buffered.length - 1);
      const currentTime = this.video.currentTime;
      const latency = bufferedEnd - currentTime;

      // Only log major latency for debugging, mpegts.js handles the jump now
      if (latency > this.MAX_LATENCY_THRESHOLD + 2) {
        console.warn("[LiveManager] High latency detected:", latency.toFixed(2));
      }
    }, 5000);
  }

  handleEvents() {
    this.player.on(mpegts.Events.ERROR, (errorType, errorDetail) => {
      console.error("[LiveManager] Stream Error:", errorType, errorDetail);
      // STRATEGY 3: Exponential Backoff Reconnection
      this.reconnect();
    });

    // STRATEGY 4: Buffer Stalled Detection
    this.player.on(mpegts.Events.STATISTICS_INFO, (info) => {
      // Give the stream at least 10 seconds to stabilize before checking for stalls
      if (Date.now() - this.initTime < 10000) return;

      if (info.speed === 0) {
        this.speedZeroCount++;
        if (this.speedZeroCount > 15) {
          // ~10-15s of 0 speed depending on report interval
          console.warn("[LiveManager] Stream stalled, reconnecting...");
          this.speedZeroCount = 0;
          this.reconnect();
        }
      } else {
        this.speedZeroCount = 0;
      }
    });
  }

  reconnect() {
    if (this.isReconnecting) return;
    this.isReconnecting = true;

    console.log("[LiveManager] Attempting to reconnect...");
    this.destroy();

    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.destroyed = false;
      this.init();
      this.isReconnecting = false;
    }, 3000);
  }

  destroy() {
    this.destroyed = true;
    if (this._pingHandler) {
      window.removeEventListener("pagehide", this._pingHandler);
      window.removeEventListener("beforeunload", this._pingHandler);
    }
    if (this.monitorInterval) {
      clearInterval(this.monitorInterval);
      this.monitorInterval = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.video.onpause = null;
    this.video.onplay = null;
    if (this.player) {
      try {
        this.player.pause();
        this.player.unload();
        this.player.detachMediaElement();
        this.player.destroy();
      } catch (e) {
        console.error("[LiveManager] Error during destroy:", e);
      }
      this.player = null;
    }
  }
}

class VodStreamManager {
  constructor(videoElement, streamUrl) {
    this.video = videoElement;
    this.url = streamUrl;
    this.player = null;
    this.monitorInterval = null;
    this.isReconnecting = false;
    this.reconnectTimer = null;
    this.destroyed = false;
  }

  init() {
    if (!mpegts.isSupported() || this.destroyed) return;

    console.log("[VodManager] Initializing VOD:", this.url);
    const absoluteUrl = new URL(this.url, window.location.href).href;
    this.player = mpegts.createPlayer(
      {
        type: "flv",
        url: absoluteUrl,
      },
      {
        enableWorker: false,
        enableStashBuffer: true,
        stashInitialSize: 1024 * 1024, // 1MB for stable buffer
        autoCleanupSourceBuffer: true,
      }
    );

    this.player.attachMediaElement(this.video);
    this.player.load();

    const playPromise = this.player.play();
    if (playPromise !== undefined) {
      playPromise.catch((error) => {
        if (error.name === "AbortError") return;
        console.error("[VodManager] Play failed:", error);
        showAutoplayOverlay(this.video);
      });
    }

    this.startMonitoring();
    this.handleEvents();
  }

  handleEvents() {
    if (!this.player) return;

    this.player.on(mpegts.Events.ERROR, (errorType, errorDetail) => {
      console.error("[VodManager] Stream Error:", errorType, errorDetail);
      // Attempt to recover by reloading at current time
      this.reconnect();
    });
  }

  reconnect() {
    if (this.isReconnecting || this.destroyed) return;
    this.isReconnecting = true;

    const currentTime = this.video.currentTime;
    console.log("[VodManager] Connection lost, recovering at:", currentTime.toFixed(2));

    // Destroy existing player but keep this manager alive
    this.destroy(false);

    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.destroyed = false;
      this.init();
      
      const onLoaded = () => {
        console.log("[VodManager] Recovery successful, seeking to:", currentTime.toFixed(2));
        this.video.currentTime = currentTime;
        this.video.play().catch(() => {});
        this.video.removeEventListener("loadedmetadata", onLoaded);
      };
      this.video.addEventListener("loadedmetadata", onLoaded);
      this.isReconnecting = false;
    }, 2000); // Wait 2s before retry
  }

  startMonitoring() {
    if (this.monitorInterval) clearInterval(this.monitorInterval);
    this.monitorInterval = setInterval(() => {
      if (!this.player || !this.video.buffered.length || this.isReconnecting) return;

      const end = this.video.buffered.end(this.video.buffered.length - 1);
      const bufferLen = end - this.video.currentTime;

      // Optimization: If buffer is excessively large for a VOD (>60s),
      // we can trigger cleanup if needed, but mpegts.js usually handles this via autoCleanup.
      if (bufferLen > 60) {
        // console.log("[VodManager] Healthy buffer:", bufferLen.toFixed(2), "s");
      }
    }, 5000);
  }

  destroy(isFinal = true) {
    if (isFinal) this.destroyed = true;
    if (this.monitorInterval) clearInterval(this.monitorInterval);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    
    if (this.player) {
      try {
        this.player.pause();
        this.player.unload();
        this.player.detachMediaElement();
        this.player.destroy();
      } catch (e) {
        console.error("[VodManager] Error during destroy:", e);
      }
      this.player = null;
    }
  }
}

function showAutoplayOverlay(video) {
  const overlay = document.getElementById("autoplay-overlay");
  const btn = document.getElementById("autoplay-unlock-btn");
  if (!overlay || !btn) return;

  overlay.classList.remove("opacity-0", "pointer-events-none");
  overlay.classList.add("opacity-100", "pointer-events-auto");

  btn.onclick = (e) => {
    e.stopPropagation();
    overlay.classList.add("opacity-0", "pointer-events-none");
    overlay.classList.remove("opacity-100", "pointer-events-auto");
    video.play().catch((err) => console.error("[Player] Manual play failed:", err));
  };
}

async function initMikuPlayer() {
  // Wait for media-chrome to be defined
  await customElements.whenDefined("media-controller");

  const video = document.getElementById("player");
  const danmakuContainer = document.getElementById("danmaku-container");
  const qualityList = document.getElementById("quality-list");
  const qualityMenu = document.getElementById("quality-menu");
  const qualityBtn = document.getElementById("quality-btn");
  const label = document.getElementById("current-quality-label"); // Note: This might be null now as we used gear icon
  const controller = document.getElementById("miku-player");

  if (!video) return;

  // 1. Danmaku Setup (Using DOM engine for pixel perfection)
  initDanmaku(video, danmakuContainer, controller);

  // 2. Quality Selection Logic
  const playerStartTime = performance.now();
  if (window.is_live) {
    setupLivePlayer(video, qualityList, label);
  } else if (window.has_dash) {
    const mpdUrl = `/video/dash/${window.current_vid}/${window.idx}/manifest.mpd`;
    console.log("[Player] Initializing VOD with dash.js:", mpdUrl);

    const player = dashjs.MediaPlayer().create();
    player.initialize(video, mpdUrl, true);
    window.dashPlayer = player;

    // Handle autoplay block
    video.play().catch((error) => {
      if (error.name === "NotAllowedError") {
        showAutoplayOverlay(video);
      }
    });

    player.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
      console.log(`[Player] DASH ready in ${(performance.now() - playerStartTime).toFixed(2)}ms`);
      updateVodDashQualityMenu(player, qualityList, label);
    });

    player.on(dashjs.MediaPlayer.events.ERROR, (e) => {
      console.error("[Player] DASH Error:", e);
    });

    setupAutoNext(video);
  } else {
    setupVodQuality(video, qualityList, label);
    setupAutoNext(video);

    // Handle FLV VODs with dedicated manager
    const currentSrc = video.src;
    if (currentSrc.includes(".flv") && mpegts.isSupported()) {
      window.vodManager = new VodStreamManager(video, currentSrc);
      window.vodManager.init();
    } else {
      // Native HTML5 video play check
      video.play().catch((error) => {
        if (error.name === "NotAllowedError") {
          showAutoplayOverlay(video);
        }
      });
    }
  }

  // 3. UI Events
  const dmBtn = document.getElementById("danmaku-toggle");
  if (dmBtn) {
    dmBtn.onclick = (e) => {
      e.stopPropagation();
      toggleDanmaku();
    };
  }

  const volumeBtn = document.getElementById("volume-btn");
  const volumeMenu = document.getElementById("volume-menu");

  if (volumeBtn && volumeMenu && controller) {
    // Custom volume trigger button
    volumeBtn.onclick = (e) => {
      e.stopPropagation();
      const isVisible = volumeMenu.classList.contains("opacity-100");
      toggleVolumeMenu(!isVisible, volumeBtn, volumeMenu, controller);
    };
    
    // Close menu when clicking outside
    document.addEventListener("click", (e) => {
      if (!volumeBtn.contains(e.target) && !volumeMenu.contains(e.target)) {
        toggleVolumeMenu(false, null, volumeMenu, controller);
      }
    });
  }

  if (qualityBtn && qualityMenu && controller) {
    qualityBtn.onclick = (e) => {
      e.stopPropagation();
      const isVisible = qualityMenu.classList.contains("opacity-100");
      toggleQualityMenu(!isVisible, qualityBtn, qualityMenu, controller);
    };
    document.addEventListener("click", (e) => {
      if (!qualityBtn.contains(e.target) && !qualityMenu.contains(e.target)) {
        toggleQualityMenu(false, null, qualityMenu, controller);
      }
    });
  }
}

function toggleVolumeMenu(show, btn, menu, controller) {
  if (!menu) return;
  if (show && btn && controller) {
    const brect = btn.getBoundingClientRect();
    const crect = controller.getBoundingClientRect();

    // Align with the volume button
    menu.style.left = brect.left - crect.left + "px";
    menu.style.bottom = crect.bottom - brect.top + 10 + "px";

    menu.classList.remove("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.add("opacity-100", "scale-100", "pointer-events-auto");
    
    // Auto-hide quality menu if open
    toggleQualityMenu(false, null, document.getElementById("quality-menu"), controller);
  } else {
    menu.classList.add("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.remove("opacity-100", "scale-100", "pointer-events-auto");
  }
}

function toggleQualityMenu(show, btn, menu, controller) {
  if (!menu) return;
  if (show && btn && controller) {
    const brect = btn.getBoundingClientRect();
    const crect = controller.getBoundingClientRect();

    menu.style.right = crect.right - brect.right + "px";
    menu.style.bottom = crect.bottom - brect.top + 10 + "px";

    menu.classList.remove("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.add("opacity-100", "scale-100", "pointer-events-auto");
    
    // Auto-hide volume menu if open
    toggleVolumeMenu(false, null, document.getElementById("volume-menu"), controller);
  } else {
    menu.classList.add("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.remove("opacity-100", "scale-100", "pointer-events-auto");
  }
}

function initDanmaku(video, container, controller) {
  if (!container || !video || !controller) return;

  const startDanmaku = (ds) => {
    window.dm = new Danmaku({
      container: container,
      media: video,
      comments: ds,
      engine: "dom",
    });

    window.dm_status = true;

    const updateSize = () => {
      if (!window.dm || !video || !container) return;

      // Get dimensions of the player controller
      const rect = controller.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      // UNIVERSAL FIX: Always use 100% of the player container.
      // This allows danmaku to flow over black bars for ANY aspect ratio (4:3, 16:9, 21:9, vertical, etc.)
      Object.assign(container.style, {
        width: "100%",
        height: "100%",
        left: "0",
        top: "0",
      });

      window.dm.resize();
    };

    const ro = new ResizeObserver(() => requestAnimationFrame(updateSize));
    ro.observe(controller);

    video.addEventListener("loadedmetadata", updateSize);
    video.addEventListener("resize", updateSize);

    // Fullscreen and window resize triggers
    ["fullscreenchange", "webkitfullscreenchange"].forEach((evt) => {
      document.addEventListener(evt, () => {
        updateSize();
        setTimeout(updateSize, 100);
      });
    });

    window.addEventListener("resize", updateSize);
    updateSize();
  };

  if (window.is_live) {
    startDanmaku([]);
  } else {
    fetch("/res/danmaku/" + window.current_vid + ":" + window.idx).then((r) =>
      r.json().then((ds) => {
        startDanmaku(ds);
      })
    );
  }
}

function setupLivePlayer(video, list, label) {
  if (window.isSettingUp) return;
  window.isSettingUp = true;

  // Get the first supported source to check format
  const firstSrc = window.supported_src && window.supported_src[0];
  const liveUrl = firstSrc
    ? `/proxy/live/${window.current_vid}_${firstSrc.quality}`
    : `/proxy/live/${window.current_vid}`;
  const isHls = firstSrc && firstSrc.url && firstSrc.url.includes(".m3u8");

  // Clean up any existing players
  if (window.hls) {
    window.hls.destroy();
    window.hls = null;
  }
  if (window.liveManager) {
    window.liveManager.destroy();
    window.liveManager = null;
  }
  // Backward compatibility cleanup
  if (window.flvPlayer) {
    window.flvPlayer.detachMediaElement();
    window.flvPlayer.destroy();
    window.flvPlayer = null;
  }

  if (isHls) {
    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: false,
        lowLatencyMode: false,
        backBufferLength: 60,
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
        liveSyncDuration: 4,
        liveMaxLatencyDuration: 8,
      });
      hls.loadSource(liveUrl);
      hls.attachMedia(video);
      window.hls = hls;

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        updateLiveQualityMenu(video, hls, null, list, label, true);
        video.play().catch(() => {});
      });

      hls.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.warn("[Player] Fatal HLS network error, attempting to recover...");
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.warn("[Player] Fatal HLS media error, attempting to recover...");
              hls.recoverMediaError();
              break;
            default:
              console.error("[Player] Unrecoverable HLS error:", data);
              hls.destroy();
              break;
          }
        }
      });

      hls.on(Hls.Events.LEVEL_SWITCHED, (event, data) => {
        if (hls.autoLevelEnabled) {
          const level = hls.levels[data.level];
          if (label) label.innerText = `自动 (${level.height}p)`;
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = liveUrl;
      video.addEventListener("loadedmetadata", () => {
        video.play().catch(() => {});
      });
    }
  } else if (mpegts.isSupported()) {
    window.liveManager = new LiveStreamManager(video, liveUrl, list, label);
    window.liveManager.init();
    updateLiveQualityMenu(video, null, window.liveManager, list, label, false);
  }
  window.isSettingUp = false;
}

function updateVodDashQualityMenu(player, list, label) {
  if (!list) return;
  list.innerHTML = "";

  const bitrates = player.getBitrateInfoListFor("video");
  if (!bitrates) return;

  // Auto option
  const autoBtn = createOption(
    "自动",
    -1,
    () => {
      player.updateSettings({
        streaming: {
          abr: {
            autoSwitchBitrate: {
              video: true,
            },
          },
        },
      });
      if (label) label.innerText = "自动";
    },
    list
  );
  if (player.getSettings().streaming.abr.autoSwitchBitrate.video) {
    autoBtn.classList.add("active");
  }
  list.appendChild(autoBtn);

  // Specific bitrates
  bitrates.forEach((info) => {
    // Info.id looks like "video_80_7"
    const parts = info.id ? info.id.split("_") : [];
    const qn = parts.length > 1 ? parseInt(parts[1]) : -1;

    // Try to find matching description from window.supported_src
    const matched = window.supported_src.find((s) => s.quality === qn);
    const name = matched ? matched.new_description : `${info.height}p`;

    const btn = createOption(
      name,
      info.bitrateIndex,
      () => {
        player.updateSettings({
          streaming: {
            abr: {
              autoSwitchBitrate: {
                video: false,
              },
            },
          },
        });
        player.setQualityFor("video", info.bitrateIndex);
        if (label) label.innerText = name;
      },
      list
    );
    list.appendChild(btn);
  });
}

function updateVodHlsQualityMenu(hls, list, label) {
  if (!list) return;
  list.innerHTML = "";

  // Auto option
  const autoBtn = createOption(
    "自动",
    -1,
    () => {
      hls.currentLevel = -1;
      if (label) label.innerText = "自动";
    },
    list
  );
  if (hls.currentLevel === -1) autoBtn.classList.add("active");
  list.appendChild(autoBtn);

  // Specific levels
  hls.levels.forEach((level, index) => {
    const name = `${level.height}p`;
    const btn = createOption(
      name,
      index,
      () => {
        hls.currentLevel = index;
        if (label) label.innerText = name;
      },
      list
    );
    if (hls.currentLevel === index) btn.classList.add("active");
    list.appendChild(btn);
  });
}

function updateLiveQualityMenu(video, hls, liveManager, list, label, isHls) {
  if (!list || !window.supported_src) return;
  list.innerHTML = "";

  if (isHls && hls) {
    // HLS Quality Logic
    const autoBtn = createOption(
      "自动",
      -1,
      () => {
        hls.currentLevel = -1;
        if (label) label.innerText = "自动";
      },
      list
    );
    if (hls.currentLevel === -1) autoBtn.classList.add("active");
    list.appendChild(autoBtn);

    hls.levels.forEach((level, index) => {
      const name = `${level.height}p`;
      const btn = createOption(
        name,
        index,
        () => {
          hls.currentLevel = index;
          if (label) label.innerText = name;
        },
        list
      );
      if (hls.currentLevel === index) btn.classList.add("active");
      list.appendChild(btn);
    });
  } else {
    // FLV / Manual Quality Switch Logic
    const firstSrc = window.supported_src[0];
    window.supported_src.forEach((src) => {
      const btn = createOption(
        src.new_description,
        src.quality,
        () => {
          const newUrl = `/proxy/live/${window.current_vid}_${src.quality}`;
          if (label) label.innerText = src.new_description;

          if (window.liveManager) {
            console.log("[Player] Switching live quality to:", src.new_description);
            window.liveManager.destroy();
            window.liveManager = new LiveStreamManager(video, newUrl, list, label);
            window.liveManager.init();
          } else if (window.hls) {
            // HLS handled above, but for consistency:
            video.src = newUrl;
            video.load();
            video.play().catch(() => {});
          } else {
            video.src = newUrl;
            video.load();
            video.play().catch(() => {});
          }
        },
        list
      );

      // Check if this is the current quality
      if (window.liveManager && window.liveManager.url.includes(`_${src.quality}`)) {
        btn.classList.add("active");
        if (label) label.innerText = src.new_description;
      } else if (
        !window.liveManager &&
        (video.src.includes(`_${src.quality}`) ||
          (src.quality === firstSrc.quality && !video.src.includes("_")))
      ) {
        btn.classList.add("active");
        if (label) label.innerText = src.new_description;
      }
      list.appendChild(btn);
    });
  }
}

function setupVodQuality(video, list, label) {
  if (!list || !window.supported_src) return;
  list.innerHTML = "";
  const sorted = [...window.supported_src].sort((a, b) => b.quality - a.quality);
  sorted.forEach((src) => {
    const btn = createOption(
      src.new_description,
      src.quality,
      () => {
        const time = video.currentTime,
          paused = video.paused;
        const ext = src.ext || "";
        const newUrl = `/proxy/video/${window.current_vid}_${window.idx}_${src.quality}${ext}`;

        if (window.vodManager) {
          window.vodManager.destroy();
          window.vodManager = null;
        }

        video.src = newUrl;

        if (ext === ".flv") {
          window.vodManager = new VodStreamManager(video, newUrl);
          window.vodManager.init();
        }

        const onLoaded = () => {
          video.currentTime = time;
          if (!paused) video.play().catch(() => {});
          video.removeEventListener("loadedmetadata", onLoaded);
        };
        video.addEventListener("loadedmetadata", onLoaded);
        if (label) label.innerText = src.new_description;
      },
      list
    );
    if (video.src.includes(`_${src.quality}`)) {
      btn.classList.add("active");
      if (label) label.innerText = src.new_description;
    }
    list.appendChild(btn);
  });
}

function createOption(text, val, onClick, list) {
  const btn = document.createElement("button");
  btn.className =
    "w-full text-left px-4 py-2.5 text-xs text-white/70 hover:bg-white/10 hover:text-white transition-all rounded-xl flex items-center justify-between group";

  const span = document.createElement("span");
  span.textContent = text;
  btn.appendChild(span);

  const icon = document.createElement("i");
  icon.className = "icon ion-md-checkmark opacity-0 group-[.active]:opacity-100";
  btn.appendChild(icon);

  btn.onclick = (e) => {
    e.stopPropagation();
    onClick();
    list.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    toggleQualityMenu(false, null, document.getElementById("quality-menu"));
  };
  return btn;
}

function toggleDanmaku() {
  if (!window.dm) return;
  const btn = document.getElementById("danmaku-toggle");
  if (window.dm_status) {
    window.dm.hide();
    if (btn) btn.style.opacity = "0.4";
  } else {
    window.dm.show();
    if (btn) btn.style.opacity = "1.0";
  }
  window.dm_status = !window.dm_status;
}

function setupAutoNext(video) {
  video.addEventListener("ended", function () {
    if (document.getElementById("continue")?.checked && ++window.idx < window.total_pages) {
      window.location.href = `/video/${window.current_vid}:${window.idx}?ato=1`;
    }
  });
}

window.initMikuPlayer = initMikuPlayer;
window.toggleDanmaku = toggleDanmaku;
document.addEventListener("DOMContentLoaded", initMikuPlayer);

/* @license-end */
