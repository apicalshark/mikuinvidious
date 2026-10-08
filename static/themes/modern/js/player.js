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
    this.streamEnded = false;

    // CONFIGURATION CONSTANTS
    this.MAX_LATENCY_THRESHOLD = 8.0;
    this.NORMAL_SPEED = 1.0;

    // UNIQUE CLIENT ID FOR DISCONNECT PINGS (128-bit, URL-safe hex)
    const _randBytes = new Uint8Array(16);
    crypto.getRandomValues(_randBytes);
    this.clientId = Array.from(_randBytes, (b) => b.toString(16).padStart(2, "0")).join("");
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
        const pingParams = new URLSearchParams({ room_id: roomId, vqn: vqn, cid: this.clientId });
        const pingUrl = `/proxy/live/disconnect?${pingParams.toString()}`;
        // sendBeacon can't set headers, so the CSRF token goes in the body
        // (form-urlencoded Blob keeps it out of access logs). Without it the
        // ping 403s and the slot lingers until the grace-period reaper.
        const csrfToken =
          document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
        if (csrfToken) {
          navigator.sendBeacon(
            pingUrl,
            new Blob([new URLSearchParams({ csrf_token: csrfToken }).toString()], {
              type: "application/x-www-form-urlencoded",
            })
          );
        } else {
          navigator.sendBeacon(pingUrl);
        }
      }
    };
    window.addEventListener("pagehide", this._pingHandler);
    window.addEventListener("beforeunload", this._pingHandler);
  }

  init() {
    if (!mpegts.isSupported() || this.destroyed) return;

    console.log("[LiveManager] Initializing stream:", this.url);
    this.streamEnded = false;
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
      if (this.streamEnded) {
        console.log("[LiveManager] Stream has ended, not reconnecting");
        this._showOverlayWhenBufferDrained();
        return;
      }
      // STRATEGY 3: Exponential Backoff Reconnection
      this.reconnect();
    });

    // Detect stream ended signal from backend
    this.player.on(mpegts.Events.METADATA_RECEIVED, (metadata) => {
      if (metadata && metadata.__stream_ended__) {
        console.log("[LiveManager] Stream ended signal received");
        this.streamEnded = true;
        this._showOverlayWhenBufferDrained();
      }
    });

    // STRATEGY 4: Buffer Stalled Detection
    this.player.on(mpegts.Events.STATISTICS_INFO, (info) => {
      // Give the stream at least 10 seconds to stabilize before checking for stalls
      if (Date.now() - this.initTime < 10000) return;

      if (this.streamEnded) return;

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
    if (this.isReconnecting || this.streamEnded) return;
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

  _showOverlayWhenBufferDrained() {
    if (this._timeUpdateHandler) {
      this.video.removeEventListener("timeupdate", this._timeUpdateHandler);
    }

    const buffered = this.video.buffered;
    if (buffered && buffered.length > 0) {
      const remaining = buffered.end(buffered.length - 1) - this.video.currentTime;
      if (remaining <= 0.5) {
        this.showStreamEndedMessage();
        return;
      }
    } else {
      this.showStreamEndedMessage();
      return;
    }

    this._timeUpdateHandler = () => {
      if (this.destroyed) {
        this.video.removeEventListener("timeupdate", this._timeUpdateHandler);
        this._timeUpdateHandler = null;
        return;
      }
      const buf = this.video.buffered;
      if (buf && buf.length > 0) {
        const rem = buf.end(buf.length - 1) - this.video.currentTime;
        if (rem <= 0.5) {
          this.video.removeEventListener("timeupdate", this._timeUpdateHandler);
          this._timeUpdateHandler = null;
          this.showStreamEndedMessage();
        }
      } else {
        this.video.removeEventListener("timeupdate", this._timeUpdateHandler);
        this._timeUpdateHandler = null;
        this.showStreamEndedMessage();
      }
    };

    this.video.addEventListener("timeupdate", this._timeUpdateHandler);
  }

  showStreamEndedMessage() {
    let overlay = document.getElementById("stream-ended-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "stream-ended-overlay";
      overlay.className = "absolute inset-0 flex items-center justify-center bg-black/80 z-10";
      // Texts assigned via textContent below through I18n.t() so translated
      // strings with quotes cannot break this markup.
      overlay.innerHTML = `
        <div class="text-center text-white">
          <div class="text-xl font-semibold mb-2"></div>
          <div class="text-sm text-white/70"></div>
        </div>
      `;
      overlay.querySelector(".text-xl").textContent = I18n.t("Stream ended");
      overlay.querySelector(".text-sm").textContent = I18n.t("Live stream has ended");
      const container = this.video.parentElement;
      if (container) {
        if (window.getComputedStyle(container).position === "static") {
          container.style.position = "relative";
        }
        container.appendChild(overlay);
      }
    }
    overlay.style.display = "flex";
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

    // Remove stream ended overlay and timeupdate listener if present
    if (this._timeUpdateHandler) {
      this.video.removeEventListener("timeupdate", this._timeUpdateHandler);
      this._timeUpdateHandler = null;
    }
    const overlay = document.getElementById("stream-ended-overlay");
    if (overlay) {
      overlay.remove();
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

class DashPlayerManager {
  constructor(videoElement, mpdUrl) {
    this.video = videoElement;
    this.mpdUrl = mpdUrl;
    this.player = null;
    this.isReconnecting = false;
    this.reconnectTimer = null;
    this.destroyed = false;
    this._errorHandler = null;
    this._userAudioChoice = false;
  }

  init() {
    if (this.destroyed || typeof dashjs === "undefined") {
      if (typeof dashjs === "undefined") {
        console.error("[DashManager] dash.js not loaded");
        this.fallbackToNative();
      }
      return;
    }
    this._userAudioChoice = false;

    console.log("[DashManager] Initializing DASH:", this.mpdUrl);
    const absoluteUrl = new URL(this.mpdUrl, window.location.href).href;
    this.player = dashjs.MediaPlayer().create();
    this.player.initialize(this.video, absoluteUrl, false);
    this.player.updateSettings({
      streaming: {
        fragmentRequestTimeout: 60000,
        // Transient home-uplink blips used to exhaust the default 3 attempts
        // on fat top-rendition segments; 5 gives them room to ride through.
        retryAttempts: {
          MediaSegment: 5,
          InitializationSegment: 5,
          IndexSegment: 5,
        },
        buffer: { fastSwitchEnabled: true },
        abr: {
          autoSwitchBitrate: { video: true, audio: false },
          // Keep abandon-on-slow-fragment explicitly enabled: with the
          // computable Content-Lengths the track proxy guarantees, this
          // lets ABR drop to a lower rendition during bandwidth dips
          // instead of stalling on the top one (dash.js#4716).
          rules: { abandonRequestsRule: { active: true } },
        },
      },
    });

    this._errorHandler = (event) => {
      const err = event && event.error;
      if (!err) return;
      // Segment-download errors (26=sidx, 27=media, 28=init) mean dash.js
      // already exhausted its own retries; its ABR drops to a lower
      // representation on its own. A full re-init here would discard the
      // buffer and replay the same failing requests in a loop.
      if (err.code === 26 || err.code === 27 || err.code === 28) {
        // Forensics for rare unreproducible failures: dash.js attaches the
        // fragment request (bytesLoaded/bytesTotal) and response (status)
        // to err.data, so the next occurrence logs enough to tell a
        // transport abort apart from an upstream short body.
        const req = (err.data && err.data.request) || {};
        const resp = (err.data && err.data.response) || {};
        console.warn(
          "[DashManager] Segment unavailable, leaving it to ABR:",
          err.code,
          err.message,
          {
            url: req.url,
            bytesLoaded: req.bytesLoaded,
            bytesTotal: req.bytesTotal,
            httpStatus: resp.status,
            durationMs:
              req.firstByteDate != null
                ? Math.round(performance.now() - req.firstByteDate)
                : undefined,
          }
        );
        return;
      }
      console.warn("[DashManager] DASH error:", err.code, err.message);
      this.reconnect();
    };
    this.player.on(dashjs.MediaPlayer.events.ERROR, this._errorHandler);

    // Audio follows the official tier map (core.*.js: video <=480p -> 30216,
    // =720p -> 30232, >=1080p -> 30280; audio ABR stays off). Re-tiered on
    // every rendered video change, exactly like Bilibili's own player — the
    // manifest lists audio worst-first, so without this dash.js would sit on
    // the ~44kbps track forever. A manual pick in the Audio row opts out
    // (sticky, like video picks locking video ABR off).
    this.player.on(
      dashjs.MediaPlayer.events.STREAM_INITIALIZED,
      () => {
        this._buildAudioMenu();
        this._applyTierAudio();
      }
    );

    // Keep the quality menu truthful: highlight whatever is actually
    // being rendered (ABR may start lower or drop down from the top
    // entry, e.g. when higher tracks fail). Audio follows the video tier.
    this._qualityHandler = (e) => {
      this._syncQualityUI();
      if (!e || e.mediaType === "video" || e.mediaType == null) this._applyTierAudio();
    };
    this.player.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, this._qualityHandler);

    this.video.play().catch((error) => {
      if (error.name === "NotAllowedError") {
        showAutoplayOverlay(this.video);
      }
    });
  }

  reconnect() {
    if (this.isReconnecting || this.destroyed) return;
    this.isReconnecting = true;
    const currentTime = this.video.currentTime;
    console.log("[DashManager] Connection lost, recovering at:", currentTime.toFixed(2));

    this.destroy(false);

    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.destroyed = false;
      this.init();
      const onLoaded = () => {
        this.video.currentTime = currentTime;
        this.video.play().catch(() => {});
        this.video.removeEventListener("loadedmetadata", onLoaded);
      };
      this.video.addEventListener("loadedmetadata", onLoaded);
      this.isReconnecting = false;
    }, 2000);
  }

  _repQn(rep) {
    // MPD Representation ids are "<type>_<qn>_<codecid>".
    const m = /_(\d+)_/.exec(String((rep && rep.id) || ""));
    return m ? parseInt(m[1], 10) : null;
  }

  _tierAudioQn(videoQn) {
    // Bundle-exact tier map (core.*.js setAudioQuality).
    if (videoQn == null) return null;
    if (videoQn <= 32) return 30216;
    if (videoQn < 80) return 30232;
    return 30280;
  }

  _bestAudio(reps) {
    let best = null;
    for (const r of reps || []) {
      if (best == null || Number(r.bandwidth) > Number(best.bandwidth)) best = r;
    }
    return best;
  }

  _applyTierAudio() {
    // Audio tier follows the rendered video quality (official behavior).
    // Skipped after a manual pick (sticky until reconnect).
    if (!this.player || this._userAudioChoice) return;
    try {
      const areps = this.player.getRepresentationsByType("audio") || [];
      if (!areps.length) return;
      const target = this._tierAudioQn(
        this._repQn(this.player.getCurrentRepresentationForType("video"))
      );
      let pick = target != null
        ? areps.find((r) => this._repQn(r) === target)
        : null;
      if (!pick) pick = this._bestAudio(areps);
      const cur = this.player.getCurrentRepresentationForType("audio");
      this._syncAudioMenu(pick.id);
      if (cur && String(cur.id) === String(pick.id)) return;
      this.player.setRepresentationForTypeById("audio", pick.id, true);
      console.log("[DashManager] Tier audio:", pick.id, pick.bandwidth);
    } catch (e) {
      console.warn("[DashManager] Could not tier audio:", e);
    }
  }

  setAudioQuality(repId) {
    // Manual audio choice from the quality menu (freedom row). Audio ABR
    // stays off; a pinned/manual pick is stable by design.
    if (!this.player) return;
    try {
      this._userAudioChoice = true;
      this.player.setRepresentationForTypeById("audio", repId, true);
      console.log("[DashManager] Audio manually set to:", repId);
    } catch (e) {
      console.warn("[DashManager] Could not set audio quality:", e);
    }
  }

  _audioLabel(rep) {
    const kbps = Math.round(Number(rep.bandwidth) / 1000) || 0;
    const codecs = String(rep.codecs || "");
    const fmt = codecs.includes("40.5")
      ? "HE-AAC"
      : codecs.includes("ec-3")
        ? "Dolby"
        : codecs.includes("flac")
          ? "FLAC"
          : "AAC";
    return `${kbps}k ${fmt}`;
  }

  _buildAudioMenu() {
    // Fills the Audio sub-view ([data-audio-options]) with one row per
    // track. Manual picks are sticky (tier-follow off until reconnect),
    // mirroring how video picks lock video ABR off.
    try {
      const opts = document.querySelector("#quality-list [data-audio-options]");
      if (!opts || !this.player) return;
      opts.innerHTML = "";
      const reps = this.player.getRepresentationsByType("audio") || [];
      const sorted = [...reps].sort((a, b) => Number(b.bandwidth) - Number(a.bandwidth));
      if (!sorted.length) return;
      // Show the section from a single track up: one row is display-only
      // but keeps the menu chrome consistent (cf. single-quality videos
      // like BV1xx411c7m9 whose Audio row would otherwise vanish).
      for (const r of sorted) {
        const btn = document.createElement("button");
        btn.className =
          "w-full text-left px-4 py-2.5 text-xs text-white/70 hover:bg-white/10 hover:text-white transition-all rounded-xl flex items-center justify-between group";
        btn.dataset.audioQn = String(r.id);
        const span = document.createElement("span");
        span.textContent = this._audioLabel(r);
        btn.appendChild(span);
        const icon = document.createElement("i");
        icon.className = "icon ion-md-checkmark opacity-0 group-[.active]:opacity-100";
        btn.appendChild(icon);
        btn.onclick = (e) => {
          e.stopPropagation();
          this.setAudioQuality(r.id);
          this._syncAudioMenu(r.id);
          toggleQualityMenu(false, null, document.getElementById("quality-menu"));
        };
        opts.appendChild(btn);
      }
      this._syncAudioMenu(null);
    } catch (e) {
      console.warn("[DashManager] Could not build audio menu:", e);
    }
  }

  _syncAudioMenu(currentId) {
    // Checkmark truth: whatever audio is rendered. currentId null = refresh.
    try {
      if (currentId == null) {
        const cur = this.player && this.player.getCurrentRepresentationForType("audio");
        currentId = cur ? cur.id : currentId;
      }
      document.querySelectorAll("#quality-list [data-audio-qn]").forEach((b) => {
        b.classList.toggle("active", currentId != null && String(b.dataset.audioQn) === String(currentId));
      });
      if (currentId != null) {
        const btn = document.querySelector(`#quality-list [data-audio-qn="${currentId}"]`);
        const av = document.querySelector("#quality-list [data-audio-value]");
        if (btn && av) av.textContent = btn.querySelector("span").textContent;
      }
    } catch (e) {
      console.warn("[DashManager] Could not sync audio menu:", e);
    }
  }

  setQuality(source) {
    if (!this.player) return;
    try {
      // Representations API of the vendored dash.js.
      const reps = this.player.getRepresentationsByType("video") || [];
      let target = null;
      if (source?.bandwidth != null) {
        target = reps.find((r) => Number(r.bandwidth) === Number(source.bandwidth));
      }
      if (!target && source?.quality != null) {
        // MPD Representation ids are "video_<qn>_<codecid>"
        target = reps.find((r) => String(r.id).includes(`_${source.quality}_`));
      }
      if (!target) {
        console.warn("[DashManager] DASH quality is not present in the manifest:", source?.quality);
        return;
      }
      this.player.updateSettings({
        streaming: { abr: { autoSwitchBitrate: { video: false } } },
      });
      this.player.setRepresentationForTypeById("video", target.id, true);
    } catch (e) {
      console.warn("[DashManager] Could not set DASH quality:", e);
    }
  }

  _syncQualityUI() {
    try {
      const rep = this.player.getCurrentRepresentationForType("video");
      if (!rep) return;
      // MPD Representation ids are "video_<qn>_<codecid>"
      const m = /_(\d+)_/.exec(String(rep.id || ""));
      const qn = m ? m[1] : String((window.supported_src || []).find((s) => Number(s.bandwidth) === Number(rep.bandwidth))?.quality ?? "");
      if (!qn) return;
      const list = document.getElementById("quality-list");
      if (list) {
        const btn = list.querySelector(`button[data-qn="${qn}"]`);
        if (btn) {
          // Scoped to the video group: the audio group keeps its own mark.
          list.querySelectorAll("button[data-video-qn]").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
        }
      }
      const label = document.getElementById("current-quality-label");
      if (label) {
        const src = (window.supported_src || []).find((s) => String(s.quality) === qn);
        if (src) label.innerText = src.new_description;
      }
      const rv = document.querySelector("#quality-list [data-resolution-value]");
      if (rv) {
        const src = (window.supported_src || []).find((s) => String(s.quality) === qn);
        if (src) rv.textContent = src.new_description;
      }
    } catch (e) {
      console.warn("[DashManager] Could not sync DASH quality UI:", e);
    }
  }

  destroy(isFinal = true) {
    if (isFinal) this.destroyed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.player) {
      try {
        if (this._errorHandler)
          this.player.off(dashjs.MediaPlayer.events.ERROR, this._errorHandler);
        if (this._qualityHandler)
          this.player.off(
            dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED,
            this._qualityHandler
          );
        this.player.reset();
      } catch (e) {
        console.error("[DashManager] Error during destroy:", e);
      }
      this.player = null;
    }
  }

  fallbackToNative() {
    // dash.js unavailable — no progressive src for on-demand DASH; show overlay
    console.error("[DashManager] DASH playback unavailable in this browser.");
  }
}

function abortVideoLoad(video) {
  return new Promise((resolve) => {
    if (!video.src && !video.currentSrc) {
      resolve();
      return;
    }
    const done = () => {
      video.removeEventListener("emptied", done);
      resolve();
    };
    video.addEventListener("emptied", done, { once: true });
    video.pause();
    video.removeAttribute("src");
    video.load();
    setTimeout(resolve, 400);
  });
}

async function triggerNativeRecovery(video) {
  if (window.isNativeRecovering) return;

  const now = Date.now();
  if (window.lastNativeRecoveryTime && now - window.lastNativeRecoveryTime < 30000) {
    console.warn("[Player] Native recovery cooldown active, skipping to prevent rate limits.");
    return;
  }
  window.lastNativeRecoveryTime = now;
  window.isNativeRecovering = true;

  const currentTime = video.currentTime;
  const recoveryUrl = new URL(video.src, window.location.href);
  recoveryUrl.searchParams.set("_r", String(now));

  console.log("[Player] Triggering native recovery at:", currentTime.toFixed(2));

  await abortVideoLoad(video);
  video.src = recoveryUrl.href;
  video.load();

  const finish = (msg) => {
    window.isNativeRecovering = false;
    console.log(msg);
  };

  const onLoaded = () => {
    const onSeeked = () => {
      video.play().catch(() => {});
      finish("[Player] Native recovery successful.");
    };
    video.addEventListener("seeked", onSeeked, { once: true });
    video.currentTime = currentTime;
    setTimeout(() => {
      if (!window.isNativeRecovering) return;
      video.removeEventListener("seeked", onSeeked);
      video.play().catch(() => {});
      finish("[Player] Native recovery successful (seek timeout).");
    }, 2000);
  };
  video.addEventListener("loadedmetadata", onLoaded, { once: true });
}

class VodBufferController {
  constructor(videoElement, minBuffer = 1.5) {
    this.video = videoElement;
    this.minBuffer = minBuffer;
    this.checkInterval = null;
    this.lastTime = 0;
    this.stuckDuration = 0;
    this.CHECK_INTERVAL_MS = 250;
  }

  start() {
    this.lastTime = this.video.currentTime;
    this.stuckDuration = 0;
    if (this.checkInterval) clearInterval(this.checkInterval);
    this.checkInterval = setInterval(() => this.checkBuffer(), this.CHECK_INTERVAL_MS);
  }

  stop() {
    if (this.checkInterval) {
      clearInterval(this.checkInterval);
      this.checkInterval = null;
    }
  }

  getBufferAhead() {
    const time = this.video.currentTime;
    for (let i = 0; i < this.video.buffered.length; i++) {
      const start = this.video.buffered.start(i);
      const end = this.video.buffered.end(i);
      if (time >= start && time <= end) {
        return end - time;
      }
    }
    return 0;
  }

  checkBuffer() {
    if (this.video.readyState < 2) {
      this.stuckDuration = 0;
      return;
    }

    if (this.video.paused || this.video.ended) {
      this.stuckDuration = 0;
      this.lastTime = this.video.currentTime;
      return;
    }

    const currentTime = this.video.currentTime;
    const bufferAhead = this.getBufferAhead();

    // 1. Show loading spinner if buffer is low
    // Removed: dispatching "waiting" event was causing danmaku to pause while video continues
    // if (bufferAhead < this.minBuffer) {
    //   this.video.dispatchEvent(new Event("waiting"));
    // }

    // 2. Detect if playhead is stuck (not advancing)
    if (Math.abs(currentTime - this.lastTime) < 0.01) {
      this.stuckDuration += this.CHECK_INTERVAL_MS;

      // If stuck for a long time, trigger recovery (skip while another recovery is active).
      if (this.stuckDuration >= 15000) {
        if (window.isNativeRecovering || this.video.error) {
          return;
        }
        console.warn(
          `[BufferControl] Playback stalled for ${this.stuckDuration / 1000}s. Triggering recovery.`
        );
        this.stuckDuration = 0;

        if (window.vodManager) {
          window.vodManager.reconnect();
        } else if (!window.hls) {
          triggerNativeRecovery(this.video);
        }
      }
    } else {
      this.stuckDuration = 0;
      this.lastTime = currentTime;
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

  // Clean up old buffer controller if it exists
  if (window.vodBufferController) {
    window.vodBufferController.stop();
    window.vodBufferController = null;
  }

  // 1. Danmaku Setup (Using DOM engine for pixel perfection)
  initDanmaku(video, danmakuContainer, controller);

  // 2. Quality Selection Logic
  const playerStartTime = performance.now();
  if (window.is_live) {
    setupLivePlayer(video, qualityList, label);
  } else {
    setupVodQuality(video, qualityList, label);
    setupAutoNext(video);

    const currentSrc = video.src;
    const flvSrc = window.supported_src?.find((s) => s.ext === ".flv");

    if (window.is_dash && window.dash_url) {
      // DASH (on-demand fragmented MP4) playback via dash.js
      window.dashManager = new DashPlayerManager(video, window.dash_url);
      window.dashManager.init();
      window.vodManager = null;
    } else if (
      currentSrc.includes(".flv") &&
      typeof mpegts !== "undefined" &&
      mpegts.isSupported()
    ) {
      // Already on FLV
      window.vodManager = new VodStreamManager(video, currentSrc);
      window.vodManager.init();
    } else if (
      flvSrc &&
      typeof mpegts !== "undefined" &&
      mpegts.isSupported() &&
      !video._flvFallbackTried
    ) {
      // MP4 is the current src but FLV is available — prefer it to avoid
      // Firefox's H.264 ConvertSampleToAVCC decode errors on Bilibili streams
      video._flvFallbackTried = true;
      const flvUrl = `/proxy/video/${window.current_vid}_${window.idx}_${flvSrc.quality}.flv`;
      console.log("[Player] Preferring FLV over MP4 to avoid H264 decode issues:", flvUrl);
      video.src = flvUrl;
      window.vodManager = new VodStreamManager(video, flvUrl);
      window.vodManager.init();
    } else {
      video.play().catch((error) => {
        if (error.name === "NotAllowedError") {
          showAutoplayOverlay(video);
        }
      });
    }
  }

  // 2.5. Buffer Controller for VOD (FLV, and progressive MP4). DASH manages its own buffer via dash.js.
  if (!window.is_live && !window.is_dash) {
    const src = video.src || "";
    const isNativeMp4 = !src.includes(".flv");
    const minBuffer = isNativeMp4 ? 4.0 : 1.5;
    window.vodBufferController = new VodBufferController(video, minBuffer);
    window.vodBufferController.start();
  }

  // 3. UI Events
  const dmBtn = document.getElementById("danmaku-toggle");
  if (dmBtn) {
    dmBtn.onclick = (e) => {
      e.stopPropagation();
      toggleDanmaku();
    };
  }

  // 4. Global Error Recovery for Native Player (MP4/Progressive)
  if (!window.vodManager && !window.hls && !window.dashManager) {
    video.onerror = () => {
      const err = video.error;
      if (!err || window.isNativeRecovering) return;

      console.warn("[Player] Native video error:", err.code, err.message);

      // Decode errors: reloading the whole MP4 often makes things worse (H264/FFmpeg/OOM).
      // Let Firefox retry via its own Range requests; only rewind once.
      // if (err.code === 3) {
      //   if (!video._decodeSeekTried && video.currentTime >= 0) {
      //     // First attempt: seek back 2s and hope Firefox resyncs
      //     video._decodeSeekTried = true;
      //     video.currentTime = Math.max(0, video.currentTime - 2);
      //     video.play().catch(() => { });
      //   } else if (video._decodeSeekTried && !video._decodeRecoveryTried) {
      //     // Seek didn't help — force a full source reload with cache-bust
      //     video._decodeRecoveryTried = true;
      //     console.warn("[Player] Seek recovery failed for decode error, trying full reload...");
      //     triggerNativeRecovery(video);
      //   }
      //   // If both attempts failed, give up silently to avoid a reload loop
      //   return;
      // }

      if (err.code === 2 || err.code === 3 || err.code === 4) {
        triggerNativeRecovery(video);
      }
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
      if (!isVisible) {
        // Always open on the main view: an option pick closes the menu
        // while its sub-view is visible, which would otherwise greet the
        // next open with a stale sub-view. Flat (live/progressive-legacy)
        // menus have no views, so this no-ops for them.
        const qlist = document.getElementById("quality-list");
        if (qlist) {
          qlist.querySelectorAll("[data-menu-view]").forEach((v) => {
            v.hidden = v.dataset.menuView !== "main";
          });
        }
      }
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

    // Explicitly start danmaku if media is not paused
    if (!video.paused) {
      window.dm.show();
    }

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

  // Get the first supported source to check format. The server owns the
  // format decision (window.live_format); URL sniffing is only a fallback
  // for Bilibili master URLs, which don't always carry an .m3u8 suffix.
  const firstSrc = window.supported_src && window.supported_src[0];
  const liveUrl = firstSrc
    ? `/proxy/live/${window.current_vid}_${firstSrc.quality}`
    : `/proxy/live/${window.current_vid}`;
  const isHls =
    window.live_format === "hls" || (firstSrc && firstSrc.url && firstSrc.url.includes(".m3u8"));

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
    if (typeof Hls !== "undefined" && Hls.isSupported()) {
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
              console.warn(
                "[Player] Fatal HLS network error, attempting to recover..."
              );
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.warn(
                "[Player] Fatal HLS media error, attempting to recover..."
              );
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
          if (label) label.innerText = I18n.t("Auto (%(height)sp)", { height: level.height });
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = liveUrl;
      video.addEventListener("loadedmetadata", () => {
        video.play().catch(() => {});
      });
    }
  } else if (typeof mpegts !== "undefined" && mpegts.isSupported()) {
    window.liveManager = new LiveStreamManager(video, liveUrl, list, label);
    window.liveManager.init();
    updateLiveQualityMenu(video, null, window.liveManager, list, label, false);
  }
  window.isSettingUp = false;
}

function updateVodHlsQualityMenu(hls, list, label) {
  if (!list) return;
  list.innerHTML = "";

  // Auto option
  const autoBtn = createOption(
    I18n.t("Auto"),
    -1,
    () => {
      hls.currentLevel = -1;
      if (label) label.innerText = I18n.t("Auto");
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
      I18n.t("Auto"),
      -1,
      () => {
        hls.currentLevel = -1;
        if (label) label.innerText = I18n.t("Auto");
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

  // Shared nested-menu builders (YouTube/Bilibili style). Both VOD paths
  // (DASH and progressive) get main rows + sub-views with back buttons;
  // only the option rows differ. Highlights stay scoped per group.
  const menu = document.getElementById("quality-menu");
  const staticTitle = menu ? menu.querySelector("[data-menu-title]") : null;
  if (staticTitle) staticTitle.style.display = "none";
  const showView = (name, dir) => {
      // FLIP the shell: switch views synchronously (no paint lands
      // mid-task), then animate #quality-list from the old box to the new
      // one so width/height glide instead of snapping. Content slides via
      // the CSS enter animation in parallel.
      const reduce =
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const prevW = list.offsetWidth;
      const prevH = list.offsetHeight;
      list.querySelectorAll("[data-menu-view]").forEach((v) => {
        const on = v.dataset.menuView === name;
        v.hidden = !on;
        if (on) v.dataset.menuDir = dir || "forward";
      });
      if (!reduce) {
        const nextW = list.offsetWidth;
        const nextH = list.offsetHeight;
        if (nextW !== prevW || nextH !== prevH) {
          list.animate(
            [
              { width: `${prevW}px`, height: `${prevH}px` },
              { width: `${nextW}px`, height: `${nextH}px` },
            ],
            { duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" }
          );
        }
      }
    };
    const rowClass =
      "w-full px-4 py-2.5 text-xs text-white/70 hover:bg-white/10 hover:text-white transition-all rounded-xl flex items-center justify-between gap-3";
    const optClass =
      "w-full text-left px-4 py-2.5 text-xs text-white/70 hover:bg-white/10 hover:text-white transition-all rounded-xl flex items-center justify-between group";
    const checkIcon = () => {
      const icon = document.createElement("i");
      icon.className = "icon ion-md-checkmark opacity-0 group-[.active]:opacity-100";
      return icon;
    };
    const backBtn = (target) => {
      const b = document.createElement("button");
      b.className = rowClass + " text-white/50";
      const l = document.createElement("span");
      l.className = "flex items-center gap-1";
      const arrow = document.createElement("i");
      arrow.className = "icon ion-ios-arrow-back";
      l.appendChild(arrow);
      const t = document.createElement("span");
      t.textContent = I18n.t("Back");
      l.appendChild(t);
      b.appendChild(l);
      b.onclick = (e) => {
        e.stopPropagation();
        showView(target, "back");
      };
      return b;
    };
    const menuRow = (labelText, valueAttr, target) => {
      const b = document.createElement("button");
      b.className = rowClass;
      const l = document.createElement("span");
      l.textContent = labelText;
      b.appendChild(l);
      const r = document.createElement("span");
      r.className = "flex items-center gap-1 text-white/50";
      const v = document.createElement("span");
      v.setAttribute(valueAttr, "1");
      r.appendChild(v);
      const chev = document.createElement("i");
      chev.className = "icon ion-ios-arrow-forward";
      r.appendChild(chev);
      b.appendChild(r);
      b.onclick = (e) => {
        e.stopPropagation();
        showView(target, "forward");
      };
      return b;
    };
    // Speed sub-view (Bilibili's rate set), shared by DASH and
    // progressive menus. Static list, synced on open.
    const buildSpeedView = () => {
      const speedView = document.createElement("div");
      speedView.dataset.menuView = "speed";
      speedView.className = "flex flex-col gap-px";
      speedView.hidden = true;
      speedView.appendChild(backBtn("main"));
      for (const rate of [2, 1.5, 1.25, 1, 0.75, 0.5]) {
        const btn = document.createElement("button");
        btn.className = optClass + " text-left group";
        btn.dataset.speedRate = String(rate);
        const span = document.createElement("span");
        span.textContent = `${rate}x`;
        btn.appendChild(span);
        btn.appendChild(checkIcon());
        btn.onclick = (e) => {
          e.stopPropagation();
          video.playbackRate = rate;
          syncSpeedMenu();
          toggleQualityMenu(false, null, document.getElementById("quality-menu"));
        };
        speedView.appendChild(btn);
      }
      return speedView;
    };
    const syncSpeedMenu = () => {
      const cur = video.playbackRate || 1;
      list.querySelectorAll("button[data-speed-rate]").forEach((b) => {
        b.classList.toggle("active", Number(b.dataset.speedRate) === cur);
      });
      const sv = list.querySelector("[data-speed-value]");
      if (sv) sv.textContent = `${cur}x`;
    };
  if (window.is_dash) {
    // Main view: navigation rows with live values.
    const main = document.createElement("div");
    main.dataset.menuView = "main";
    main.className = "flex flex-col gap-px";
    main.appendChild(menuRow(I18n.t("Resolution"), "data-resolution-value", "video"));
    main.appendChild(menuRow(I18n.t("Audio"), "data-audio-value", "audio"));
    const speedRow = menuRow(I18n.t("Playback speed"), "data-speed-value", "speed");
    // The control-bar cycle button can change the rate behind our back;
    // re-sync the highlight every time the row is opened.
    speedRow.onclick = (e) => {
      e.stopPropagation();
      syncSpeedMenu();
      showView("speed", "forward");
    };
    main.appendChild(speedRow);
    list.appendChild(main);
    // Video sub-view.
    const videoView = document.createElement("div");
    videoView.dataset.menuView = "video";
    videoView.className = "flex flex-col gap-px";
    videoView.hidden = true;
    videoView.appendChild(backBtn("main"));
    sorted.forEach((src, i) => {
      // DASH switching resolves the API quality against MPD representations.
      // data-video-qn scopes the active highlight to this group (see
      // _syncQualityUI); data-qn is kept for the rendered-quality lookup.
      const btn = document.createElement("button");
      btn.className = optClass + " text-left group";
      btn.dataset.videoQn = String(src.quality);
      btn.dataset.qn = String(src.quality);
      const span = document.createElement("span");
      span.textContent = src.new_description;
      btn.appendChild(span);
      btn.appendChild(checkIcon());
      if (i === 0) btn.classList.add("active");
      btn.onclick = (e) => {
        e.stopPropagation();
        window.dashManager.setQuality(src);
        if (label) label.innerText = src.new_description;
        const rv = list.querySelector("[data-resolution-value]");
        if (rv) rv.textContent = src.new_description;
        list.querySelectorAll("button[data-video-qn]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        toggleQualityMenu(false, null, document.getElementById("quality-menu"));
      };
      videoView.appendChild(btn);
    });
    list.appendChild(videoView);
    // Audio sub-view: back button now, tracks once the manifest parses
    // (DashPlayerManager._buildAudioMenu fills [data-audio-options]).
    const audioView = document.createElement("div");
    audioView.dataset.menuView = "audio";
    audioView.className = "flex flex-col gap-px";
    audioView.hidden = true;
    audioView.appendChild(backBtn("main"));
    const audioOpts = document.createElement("div");
    audioOpts.dataset.audioOptions = "1";
    audioOpts.className = "flex flex-col gap-px";
    audioView.appendChild(audioOpts);
    list.appendChild(audioView);
    list.appendChild(buildSpeedView());
    syncSpeedMenu();
    // Prefill with the top entry; live sync corrects both rows as the
    // player renders (video) and tiers (audio).
    const rv0 = list.querySelector("[data-resolution-value]");
    if (rv0 && sorted[0]) rv0.textContent = sorted[0].new_description;
    return;
  }

  // Progressive (native MP4 / FLV) menu: same nested shape as DASH, minus
  // the Audio row (muxed file, no separate track to choose).
  const main = document.createElement("div");
  main.dataset.menuView = "main";
  main.className = "flex flex-col gap-px";
  main.appendChild(menuRow(I18n.t("Resolution"), "data-resolution-value", "video"));
  const speedRow = menuRow(I18n.t("Playback speed"), "data-speed-value", "speed");
  speedRow.onclick = (e) => {
    e.stopPropagation();
    syncSpeedMenu();
    showView("speed", "forward");
  };
  main.appendChild(speedRow);
  list.appendChild(main);
  const videoView = document.createElement("div");
  videoView.dataset.menuView = "video";
  videoView.className = "flex flex-col gap-px";
  videoView.hidden = true;
  videoView.appendChild(backBtn("main"));
  sorted.forEach((src) => {
    const btn = document.createElement("button");
    btn.className = optClass + " text-left group";
    btn.dataset.videoQn = String(src.quality);
    const span = document.createElement("span");
    span.textContent = src.new_description;
    btn.appendChild(span);
    btn.appendChild(checkIcon());
    btn.onclick = (e) => {
      e.stopPropagation();
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
      const rv = list.querySelector("[data-resolution-value]");
      if (rv) rv.textContent = src.new_description;
      list.querySelectorAll("button[data-video-qn]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      toggleQualityMenu(false, null, document.getElementById("quality-menu"));
    };
    if (video.src.includes(`_${src.quality}`)) {
      btn.classList.add("active");
      if (label) label.innerText = src.new_description;
      const rv = list.querySelector("[data-resolution-value]");
      if (rv) rv.textContent = src.new_description;
    }
    videoView.appendChild(btn);
  });
  list.appendChild(videoView);
  list.appendChild(buildSpeedView());
  syncSpeedMenu();
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
