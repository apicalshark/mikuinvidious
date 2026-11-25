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
        enableStashBuffer: false,
        fixAudioTimestampGap: false,
        autoCleanupSourceBuffer: true,
        autoCleanupMaxBackwardDuration: 60,
        autoCleanupMinBackwardDuration: 30,
        isLive: true,
        liveSync: true,
        liveSyncMaxLatency: 5.0,
        liveSyncTargetLatency: 3.0,
        liveSyncPlaybackRate: 1.05,
        liveBufferLatencyChasing: false,
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
    // Expired-signature recovery (mirrors the official player's
    // expiry-stamped segment errors + prefetchPlayUrl): consecutive segment
    // failures while playback position is frozen mean no rendition can serve
    // (all share one URL expiry window), so re-init from a freshly-fetched
    // manifest instead of leaving it to ABR.
    this._segErrStreak = 0;
    this._lastSegErrPos = 0;
    this._lastFreshMpdAt = 0;
    this._lastResignAt = 0;
    this._seekPlaybackStarted = false;
    this._pendingSeekTime = null;
    this._manualVideoLock = false;
    this._onVideoPlaying = null;
    this._onVideoSeeking = null;
    this._onVideoTimeUpdate = null;
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
    this._segErrStreak = 0;
    this._seekPlaybackStarted = false;
    this._pendingSeekTime = null;

    console.log("[DashManager] Initializing DASH:", this.mpdUrl);
    const absoluteUrl = new URL(this.mpdUrl, window.location.href).href;
    this.player = dashjs.MediaPlayer().create();
    if (typeof this.player.setCustomInitialTrackSelectionFunction === "function") {
      this.player.setCustomInitialTrackSelectionFunction((tracks) => {
        if (!Array.isArray(tracks) || tracks.length <= 1 || tracks[0]?.type !== "video") {
          return tracks;
        }
        const order = this._codecPriorityOrder(this._preferredCodec());
        for (const wanted of order) {
          const match = tracks.find((t) => this._trackCodecLabel(t) === wanted);
          if (match) return [match];
        }
        return [tracks[0]];
      });
    }
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
        buffer: {
          fastSwitchEnabled: true,
          // Backward only: keep everything watched while the page is open
          bufferToKeep: 3600,
          bufferPruningInterval: 60,
        },
        gaps: {
          jumpGaps: true,
          jumpLargeGaps: false,
          smallGapLimit: 1.5,
        },
        abr: {
          autoSwitchBitrate: { video: !this._lockVideo(), audio: false },
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
        // ...unless failures are systematic: consecutive segment errors
        // while playback position is frozen mean every rendition fails —
        // all tracks share one signed-URL expiry window, so ABR cannot
        // route around it. Recover with a freshly-fetched manifest, like
        // the official player's expiry-stamped errors + prefetchPlayUrl.
        // (Progress resets the streak, so isolated sick-edge fragments
        // keep the cheap ABR-only path.)
        const pos = this.video ? this.video.currentTime : 0;
        if (pos > (this._lastSegErrPos || 0) + 1) this._segErrStreak = 0;
        this._lastSegErrPos = pos;
        this._segErrStreak = (this._segErrStreak || 0) + 1;
        if (this._segErrStreak >= 3 && this._freshReconnectAllowed()) {
          this._segErrStreak = 0;
          this._lastFreshMpdAt = Date.now();
          if (resp.status === 403 && this._resignMpdUrlAndReconnect(true)) {
            return;
          }
          try {
            const u = new URL(this.mpdUrl, window.location.href);
            u.searchParams.set("fresh", "1");
            this.mpdUrl = u.pathname + u.search + u.hash;
          } catch (e) {
            /* keep the current manifest URL */
          }
          console.warn(
            "[DashManager] Persistent segment failures, re-initializing with fresh playurl"
          );
          this.reconnect();
        }
        return;
      }
      const errResp = err.data && err.data.response;
      if (errResp && errResp.status === 403) {
        const wantFresh = String(this.mpdUrl || "").includes("fresh=1");
        if (this._resignMpdUrlAndReconnect(wantFresh)) {
          return;
        }
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
    this.player.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
      this._buildCodecMenu();
      this._buildVideoMenu();
      this._buildAudioMenu();
      this._applyTierAudio();
      this._applyLockVideo();
      this._syncQualityUI();
    });

    // Keep the quality menu truthful: highlight whatever is actually
    // being rendered (ABR may start lower or drop down from the top
    // entry, e.g. when higher tracks fail). Audio follows the video tier.
    this._qualityHandler = (e) => {
      this._syncQualityUI();
      if (e && e.mediaType === "audio") {
        this._syncAudioMenu();
      }
      if (!e || e.mediaType === "video" || e.mediaType == null) this._applyTierAudio();
    };
    this.player.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, this._qualityHandler);

    this._trackChangeHandler = (e) => {
      if (!e || e.mediaType === "video") {
        this._buildVideoMenu();
        this._syncCodecMenu();
        this._syncQualityUI();
      }
    };
    this.player.on(dashjs.MediaPlayer.events.TRACK_CHANGE_RENDERED, this._trackChangeHandler);

    this._onVideoPlaying = () => {
      this._seekPlaybackStarted = true;
    };
    this._onVideoSeeking = () => {
      if (!this.player || !Number.isFinite(this.video.currentTime)) {
        return;
      }
      this._realignBufferedSeek(this.video.currentTime);
      if (!this._seekPlaybackStarted) {
        return;
      }
      this._pendingSeekTime = this.video.currentTime;
      const keepLocked = this._lockVideo() || this._manualVideoLock;
      this.player.updateSettings({
        streaming: {
          buffer: {
            fastSwitchEnabled: true,
            bufferToKeep: 3600,
            bufferPruningInterval: 60,
            bufferTimeDefault: 2,
            bufferTimeAtTopQuality: 2,
            bufferTimeAtTopQualityLongForm: 2,
          },
          abr: { maxBitrate: { video: keepLocked ? -1 : 1500 } },
        },
      });
    };
    this._onVideoTimeUpdate = () => {
      if (
        this._pendingSeekTime === null ||
        !this.player ||
        this.video.paused ||
        this.video.seeking ||
        this.video.currentTime < this._pendingSeekTime + 1.5
      ) {
        return;
      }
      this._pendingSeekTime = null;
      this.player.updateSettings({
        streaming: {
          buffer: {
            fastSwitchEnabled: true,
            bufferToKeep: 3600,
            bufferPruningInterval: 60,
            bufferTimeDefault: 18,
            bufferTimeAtTopQuality: 30,
            bufferTimeAtTopQualityLongForm: 60,
          },
          abr: { maxBitrate: { video: -1 } },
        },
      });
    };
    this.video.addEventListener("playing", this._onVideoPlaying);
    this.video.addEventListener("seeking", this._onVideoSeeking);
    this.video.addEventListener("timeupdate", this._onVideoTimeUpdate);

    this.video.play().catch((error) => {
      if (error.name === "NotAllowedError") {
        showAutoplayOverlay(this.video);
      }
    });
  }

  _freshReconnectAllowed() {
    // Cap fresh-manifest recovery (official player maxResumeTimes equivalent):
    // at most one fresh playurl fetch per 10 minutes. Past that, failures are
    // not expiry-shaped — keep ABR-only behavior instead of hammering the
    // playurl endpoint (risk-control exposure).
    return !this._lastFreshMpdAt || Date.now() - this._lastFreshMpdAt > 10 * 60 * 1000;
  }

  _resignMpdUrlAndReconnect(wantFresh = false) {
    if (this._lastResignAt && Date.now() - this._lastResignAt < 30 * 1000) {
      return false;
    }
    this._lastResignAt = Date.now();
    const fallbackReconnect = () => {
      if (this.destroyed) return;
      if (wantFresh) {
        try {
          const u = new URL(this.mpdUrl, window.location.href);
          u.searchParams.set("fresh", "1");
          this.mpdUrl = u.pathname + u.search + u.hash;
        } catch (e) {
          /* keep un-modified manifest URL */
        }
      }
      this.reconnect();
    };
    fetch(window.location.href, { credentials: "same-origin" })
      .then((res) => (res.ok ? res.text() : ""))
      .then((html) => {
        if (this.destroyed) return;
        if (!html) {
          fallbackReconnect();
          return;
        }
        const doc = new DOMParser().parseFromString(html, "text/html");
        const wrapper = doc.querySelector("[data-dash-url]");
        const freshUrl = wrapper ? wrapper.getAttribute("data-dash-url") : "";
        if (!freshUrl) {
          fallbackReconnect();
          return;
        }
        window.dash_url = freshUrl;
        let targetUrl = freshUrl;
        if (wantFresh) {
          try {
            const u = new URL(freshUrl, window.location.href);
            u.searchParams.set("fresh", "1");
            targetUrl = u.pathname + u.search + u.hash;
          } catch (e) {
            /* keep un-modified signed URL */
          }
        }
        this.mpdUrl = targetUrl;
        this.reconnect();
      })
      .catch(() => {
        fallbackReconnect();
      });
    return true;
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

  _lockVideo() {
    // "Lock video resolution" defaults OFF: only an explicit "1" opts in
    // (fresh visitors keep ABR). Mirrors _pinBestAudio with inverted default.
    try {
      if (typeof helpers !== "undefined" && helpers.storage) {
        const v = helpers.storage.get("lock_video");
        if (v === "0") return false;
        if (v === "1") return true;
      }
    } catch (e) {
      console.warn("[DashManager] Could not read lock_video pref:", e);
    }
    return /(?:^|;\s*)lock_video=1(?:;|$)/.test(document.cookie || "");
  }

  _applyLockVideo() {
    // Pinned video (pref): stick to the best rendition with ABR off.
    // Manual quality-menu picks bypass this (sticky by design, like audio).
    if (!this.player || !this._lockVideo()) return;
    try {
      const vreps = this.player.getRepresentationsByType("video") || [];
      if (!vreps.length) return;
      let best = null;
      for (const r of vreps) {
        if (best == null || Number(r.bandwidth) > Number(best.bandwidth)) best = r;
      }
      if (!best) return;
      const cur = this.player.getCurrentRepresentationForType("video");
      if (cur && String(cur.id) === String(best.id)) return;
      this.player.setRepresentationForTypeById("video", best.id, true);
      console.log("[DashManager] Locked video:", best.id, best.bandwidth);
    } catch (e) {
      console.warn("[DashManager] Could not lock video:", e);
    }
  }

  _pinBestAudio() {
    // "Pin best audio quality" defaults ON: only an explicit "0" opts out
    // (fresh visitors get the best track with no action needed).
    try {
      if (typeof helpers !== "undefined" && helpers.storage) {
        const v = helpers.storage.get("best_audio");
        if (v === "0") return false;
        if (v === "1") return true;
      }
    } catch (e) {
      console.warn("[DashManager] Could not read best_audio pref:", e);
    }
    return !/(?:^|;\s*)best_audio=0(?:;|$)/.test(document.cookie || "");
  }

  _applyTierAudio() {
    // Audio tier follows the rendered video quality (official behavior).
    // Skipped after a manual pick (sticky until reconnect), and skipped
    // entirely when the "Pin best audio quality" pref is on — then the
    // best available track is pinned instead of tier-following.
    if (!this.player || this._userAudioChoice) return;
    try {
      const areps = this.player.getRepresentationsByType("audio") || [];
      if (!areps.length) return;
      let pick = null;
      if (!this._pinBestAudio()) {
        const target = this._tierAudioQn(
          this._repQn(this.player.getCurrentRepresentationForType("video"))
        );
        pick = target != null ? areps.find((r) => this._repQn(r) === target) : null;
      }
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
    try {
      if (!this.player) return;
      const reps = this.player.getRepresentationsByType("audio") || [];
      const sorted = [...reps].sort((a, b) => Number(b.bandwidth) - Number(a.bandwidth));
      if (!sorted.length) return;
      const cur = this.player.getCurrentRepresentationForType("audio");
      const tracks = sorted.map((r) => ({ id: r.id, label: this._audioLabel(r) }));
      window.MikuPlayerUI?.setAudioTracks(tracks, cur ? cur.id : null, (id) => {
        this.setAudioQuality(id);
      });
    } catch (e) {
      console.warn("[DashManager] Could not build audio menu:", e);
    }
  }

  _syncAudioMenu(currentId) {
    try {
      if (currentId == null) {
        const cur = this.player && this.player.getCurrentRepresentationForType("audio");
        currentId = cur ? cur.id : currentId;
      }
      window.MikuPlayerUI?.syncAudioTrack(currentId);
    } catch (e) {
      console.warn("[DashManager] Could not sync audio menu:", e);
    }
  }

  _preferredCodec() {
    const m = (document.cookie || "").match(/(?:^|;\s*)preferred_codec=([^;]+)/);
    const cv = m ? String(m[1]).toLowerCase() : "";
    if (cv === "av1" || cv === "hevc" || cv === "avc" || cv === "auto") return cv;
    try {
      if (typeof helpers !== "undefined" && helpers.storage) {
        const v = String(helpers.storage.get("preferred_codec") || "").toLowerCase();
        if (v === "av1" || v === "hevc" || v === "avc" || v === "auto") return v;
      }
    } catch (e) {
      void e;
    }
    return "auto";
  }

  _codecPriorityOrder(pref) {
    if (pref === "hevc") return ["HEVC", "AVC", "AV1"];
    if (pref === "avc") return ["AVC", "HEVC", "AV1"];
    return ["AV1", "HEVC", "AVC"];
  }

  _trackKey(track, fallbackIdx) {
    return String(track?.id ?? track?.index ?? fallbackIdx);
  }

  _trackCodecLabel(track) {
    if (!track) return "VIDEO";
    const firstId = String(track?.bitrateList?.[0]?.id || "");
    const raw = String(track?.codec || track?.mimeType || "").toLowerCase();
    if (raw.includes("av01") || firstId.endsWith("_13")) return "AV1";
    if (raw.includes("hev") || raw.includes("hvc") || firstId.endsWith("_12")) return "HEVC";
    if (raw.includes("avc") || firstId.endsWith("_7")) return "AVC";
    const cleaned = raw.replace(/.*codecs="([^"]+)".*/, "$1");
    return cleaned ? cleaned.split(".")[0].toUpperCase() : "VIDEO";
  }

  _buildCodecMenu() {
    try {
      if (window.is_audio || !this.player) return;
      const tracks = this.player.getTracksFor("video") || [];
      if (tracks.length <= 1) {
        this._videoTracks = tracks;
        window.MikuPlayerUI?.setVideoCodecs([], null, null);
        return;
      }
      this._videoTracks = tracks;
      const cur = this.player.getCurrentTrackFor("video");
      const curKey = cur ? this._trackKey(cur, Math.max(tracks.indexOf(cur), 0)) : null;
      const items = tracks.map((trk, i) => ({
        id: this._trackKey(trk, i),
        label: this._trackCodecLabel(trk),
      }));
      window.MikuPlayerUI?.setVideoCodecs(items, curKey, (id) => {
        this.setVideoCodec(id);
      });
    } catch (e) {
      console.warn("[DashManager] Could not build codec menu:", e);
    }
  }

  _syncCodecMenu() {
    try {
      if (window.is_audio || !this.player) return;
      const tracks = this._videoTracks || this.player.getTracksFor("video") || [];
      const cur = this.player.getCurrentTrackFor("video");
      window.MikuPlayerUI?.syncVideoCodec(
        cur ? this._trackKey(cur, Math.max(tracks.indexOf(cur), 0)) : null
      );
    } catch (e) {
      console.warn("[DashManager] Could not sync codec menu:", e);
    }
  }

  setVideoCodec(trackKey) {
    if (!this.player) return;
    try {
      const tracks = this._videoTracks || this.player.getTracksFor("video") || [];
      const target = tracks.find((t, i) => this._trackKey(t, i) === String(trackKey));
      if (!target) return;
      const cur = this.player.getCurrentTrackFor("video");
      if (cur && this._trackKey(cur, Math.max(tracks.indexOf(cur), 0)) === String(trackKey)) return;
      const qn = this._repQn(this.player.getCurrentRepresentationForType("video"));
      this.player.setCurrentTrack(target);
      this._buildVideoMenu();
      this._syncCodecMenu();
      if (qn != null && (this._manualVideoLock || this._lockVideo())) {
        this.setQuality({ quality: qn });
      }
      this._syncQualityUI();
    } catch (e) {
      console.warn("[DashManager] Could not switch video codec:", e);
    }
  }

  _videoCodecLabel(rep) {
    if (!rep) return "";
    const codecs = String(rep.codecs || "").toLowerCase();
    const id = String(rep.id || "");
    if (codecs.includes("av01") || id.endsWith("_13")) return "AV1";
    if (codecs.includes("hev") || codecs.includes("hvc") || id.endsWith("_12")) return "HEVC";
    if (codecs.includes("avc") || id.endsWith("_7")) return "AVC";
    return "";
  }

  _cleanQualityLabel(text) {
    if (!text) return "";
    return String(text)
      .replace(
        /\s*(?:超高清|超清|高清|清晰|流畅|流暢|标清|標清|极速|極速|高码率|高碼率|高帧率|高幀率)/g,
        ""
      )
      .replace(/(\d+)P\b/g, "$1p")
      .trim();
  }

  _parseFrameRate(raw) {
    if (raw == null || raw === "") return 0;
    const str = String(raw).trim();
    if (str.includes("/")) {
      const [num, den] = str.split("/");
      const n = Number(num);
      const d = Number(den);
      return Number.isFinite(n) && Number.isFinite(d) && d > 0 ? n / d : 0;
    }
    const v = Number(str);
    return Number.isFinite(v) ? v : 0;
  }

  _videoRepLabel(rep) {
    if (!rep) return "";
    const qn = this._repQn(rep);
    const qnStr = qn != null ? String(qn) : "";
    const src = (window.supported_src || []).find((s) => String(s.quality) === qnStr);
    let base =
      rep.height >= 4320
        ? "8K"
        : rep.height >= 2160
          ? "4K"
          : rep.height > 0
            ? `${rep.height}p`
            : this._cleanQualityLabel(src?.new_description) || qnStr;
    const fps = this._parseFrameRate(rep.frameRate);
    if (fps > 30) base += ` ${Math.round(fps)}fps`;
    const codec = this._videoCodecLabel(rep);
    return codec ? `${base} (${codec})` : base;
  }

  _buildVideoMenu() {
    try {
      if (window.is_audio || !this.player) return;
      const reps = this.player.getRepresentationsByType("video") || [];
      if (!reps.length) return;
      const sorted = [...reps].sort((a, b) => {
        const qDiff = Number(this._repQn(b) || 0) - Number(this._repQn(a) || 0);
        if (qDiff !== 0) return qDiff;
        return Number(b.bandwidth || 0) - Number(a.bandwidth || 0);
      });
      const cur = this.player.getCurrentRepresentationForType("video");
      const tracks = sorted.map((r) => ({
        id: r.id,
        qn: String(this._repQn(r) || ""),
        label: this._videoRepLabel(r),
      }));
      window.MikuPlayerUI?.setVideoTracks(tracks, cur ? cur.id : null, (trk) => {
        this.setQuality({ repId: trk.id, quality: trk.qn, new_description: trk.label });
      });
    } catch (e) {
      console.warn("[DashManager] Could not build video menu:", e);
    }
  }

  setQuality(source) {
    if (!this.player) return;
    try {
      // Representations API of the vendored dash.js.
      const reps = this.player.getRepresentationsByType("video") || [];
      let target = null;
      if (source?.repId != null) {
        target = reps.find((r) => String(r.id) === String(source.repId));
      }
      if (!target && source?.bandwidth != null) {
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
      this._manualVideoLock = true;
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
      const m = /_(\d+)_/.exec(String(rep.id || ""));
      const qn = m
        ? m[1]
        : String(
            (window.supported_src || []).find((s) => Number(s.bandwidth) === Number(rep.bandwidth))
              ?.quality ?? ""
          );
      if (!qn) return;
      const fullLabel = this._videoRepLabel(rep);
      const label = document.getElementById("current-quality-label");
      if (label && fullLabel) label.innerText = fullLabel;
      window.MikuPlayerUI?.syncVideoQuality(qn, fullLabel, rep.id);
    } catch (e) {
      console.warn("[DashManager] Could not sync DASH quality UI:", e);
    }
  }

  _realignBufferedSeek(seekTime) {
    const seekId = (this._bufferedSeekId = Symbol());
    try {
      const stream = this.player?.getActiveStream?.();
      const processors = stream?.getStreamProcessors?.() || [];
      for (const sp of processors) {
        const bc = sp?.getBufferController?.();
        const sc = sp?.getScheduleController?.();
        if (!bc || typeof bc.hasBufferAtTime !== "function" || !bc.hasBufferAtTime(seekTime)) {
          continue;
        }
        const contEnd = bc.getContinuousBufferTimeForTargetTime?.(seekTime);
        if (!Number.isFinite(contEnd)) {
          continue;
        }
        const ranges = bc.getBuffer?.()?.getAllBufferRanges?.();
        const lastEnd = ranges && ranges.length > 0 ? ranges.end(ranges.length - 1) : contEnd;
        const hasDisjointFuture = lastEnd > contEnd + 0.1;
        const bufferingTime = sp.getBufferingTime?.();
        const cursorDrifted =
          Number.isFinite(bufferingTime) && Math.abs(bufferingTime - contEnd) > 0.5;
        if (hasDisjointFuture || cursorDrifted || bc.getIsBufferingCompleted?.()) {
          sp.getFragmentModel?.()?.abortRequests?.();
          const clearPromise =
            hasDisjointFuture && typeof bc.clearBuffers === "function"
              ? bc.clearBuffers([{ start: contEnd + 0.05, end: lastEnd + 1 }])
              : Promise.resolve();
          Promise.resolve(clearPromise)
            .catch(() => {})
            .then(() => {
              if (this.destroyed || !this.player || this._bufferedSeekId !== seekId) return;
              const freshEnd = bc.getContinuousBufferTimeForTargetTime?.(seekTime);
              const nextTarget = Number.isFinite(freshEnd) ? freshEnd : contEnd;
              if (nextTarget < (this.video?.duration || Infinity) - 0.5) {
                bc.setIsBufferingCompleted?.(false);
              }
              sp.setExplicitBufferingTime?.(nextTarget);
              bc.setSeekTarget?.(nextTarget);
              sc?.setInitSegmentRequired?.(true);
              sc?.startScheduleTimer?.(0);
            });
        }
      }
    } catch (e) {
      console.warn("[DashManager] Could not realign buffered seek:", e);
    }
  }

  destroy(isFinal = true) {
    if (isFinal) this.destroyed = true;
    this._bufferedSeekId = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.video) {
      if (this._onVideoPlaying) this.video.removeEventListener("playing", this._onVideoPlaying);
      if (this._onVideoSeeking) this.video.removeEventListener("seeking", this._onVideoSeeking);
      if (this._onVideoTimeUpdate)
        this.video.removeEventListener("timeupdate", this._onVideoTimeUpdate);
    }
    this._onVideoPlaying = null;
    this._onVideoSeeking = null;
    this._onVideoTimeUpdate = null;
    this._seekPlaybackStarted = false;
    this._pendingSeekTime = null;
    if (this.player) {
      try {
        if (this._errorHandler)
          this.player.off(dashjs.MediaPlayer.events.ERROR, this._errorHandler);
        if (this._qualityHandler)
          this.player.off(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, this._qualityHandler);
        if (this._trackChangeHandler)
          this.player.off(
            dashjs.MediaPlayer.events.TRACK_CHANGE_RENDERED,
            this._trackChangeHandler
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
  const video = document.getElementById("player");
  const danmakuContainer = document.getElementById("danmaku-container");
  const qualityList = document.getElementById("quality-list");
  const label = document.getElementById("current-quality-label");
  const controller = document.getElementById("miku-player");

  if (!video) return;

  if (!window.is_live && !video._zeroSeekClamped) {
    try {
      const protoDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "currentTime");
      if (protoDesc && typeof protoDesc.set === "function") {
        Object.defineProperty(video, "currentTime", {
          get() {
            return protoDesc.get.call(this);
          },
          set(v) {
            const t = Number(v);
            protoDesc.set.call(this, t <= 0 && Number(this.duration) > 1 ? 0.1 : v);
          },
          configurable: true,
        });
        video._zeroSeekClamped = true;
      }
    } catch (e) {
      console.warn("[Player] Could not install zero-seek clamp:", e);
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

  if (controller && window.MikuPlayerUI && typeof window.MikuPlayerUI.mount === "function") {
    window.MikuPlayerUI.mount(controller, video, {
      isLive: Boolean(window.is_live),
      isAudio: Boolean(window.is_audio),
    });
  }

  if (window.vodBufferController) {
    window.vodBufferController.stop();
    window.vodBufferController = null;
  }

  // 1. Danmaku Setup (Using DOM engine for pixel perfection)
  if (!window.is_audio) {
    initDanmaku(video, danmakuContainer, controller);
  }

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

      if (err.code === 2 || err.code === 3 || err.code === 4) {
        triggerNativeRecovery(video);
      }
    };
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
    window.MikuPlayerUI?.applyDanmakuScale(window.dm);

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
    const danmakuUrl = video?.dataset?.danmakuUrl;
    if (!danmakuUrl) {
      startDanmaku([]);
    } else {
      fetch(danmakuUrl)
        .then((r) => {
          if (!r.ok) throw new Error("Danmaku HTTP " + r.status);
          return r.json();
        })
        .then((ds) => {
          startDanmaku(Array.isArray(ds) ? ds : []);
        })
        .catch((err) => {
          console.warn("Failed to load danmaku:", err);
          startDanmaku([]);
        });
    }
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
    const hlsUrl = `${liveUrl}${liveUrl.includes("?") ? "&" : "?"}format=hls`;
    if (typeof Hls !== "undefined" && Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: false,
        lowLatencyMode: false,
        progressive: true,
        fetchSetup: (context, initParams) => {
          if (
            context.type === "manifest" ||
            context.type === "level" ||
            context.type === "audioTrack" ||
            context.type === "subtitleTrack"
          ) {
            initParams.cache = "no-store";
          }
          return new Request(context.url, initParams);
        },
        contentSteeringController: null,
        liveDurationInfinity: true,
        backBufferLength: 60,
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
        liveSyncDuration: 3,
        liveMaxLatencyDuration: 6,
      });
      hls.loadSource(hlsUrl);
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
          const autoText = I18n.t("Auto (%(height)sp)", { height: level.height });
          if (label) label.innerText = autoText;
          window.MikuPlayerUI?.syncLiveQuality(autoText);
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsUrl;
      window.MikuPlayerUI?.setupLiveMenu({ items: [] });
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

function formatHlsLiveLevelLabel(level) {
  const parts = [];
  if (level.height) parts.push(`${level.height}p`);
  const display = level.attrs && level.attrs["BILI-DISPLAY"];
  if (display) {
    parts.push(display);
  } else if (level.bitrate) {
    parts.push(`${(level.bitrate / 1e6).toFixed(1)}M`);
  }
  const rawPathway =
    level.pathwayId ||
    (level.attrs && level.attrs["PATHWAY-ID"]) ||
    (Array.isArray(level.url) ? level.url[0] : level.url) ||
    "";
  let pathwayLabel = "";
  if (rawPathway) {
    try {
      const host = new URL(rawPathway).hostname;
      pathwayLabel = host.split(".")[0] || host;
    } catch {
      pathwayLabel = rawPathway;
    }
  }
  const base = parts.join(" ") || `${level.height || 0}p`;
  return pathwayLabel ? `${base} · ${pathwayLabel}` : base;
}

function updateLiveQualityMenu(video, hls, liveManager, list, label, isHls) {
  if (!window.supported_src) return;

  if (isHls && hls) {
    const items = [
      {
        label: I18n.t("Auto"),
        value: -1,
        active: hls.currentLevel === -1,
      },
      ...hls.levels
        .map((level, index) => ({ level, index }))
        .reverse()
        .map(({ level, index }) => ({
          label: formatHlsLiveLevelLabel(level),
          value: index,
          active: hls.currentLevel === index,
        })),
    ];
    window.MikuPlayerUI?.setupLiveMenu({
      items,
      onSelect: (item) => {
        hls.currentLevel = Number(item.value);
        if (label) label.innerText = item.label;
      },
    });
  } else {
    const firstSrc = window.supported_src[0];
    const items = window.supported_src.map((src) => {
      const active = Boolean(
        (window.liveManager && window.liveManager.url.includes(`_${src.quality}`)) ||
        (!window.liveManager &&
          (video.src.includes(`_${src.quality}`) ||
            (firstSrc && src.quality === firstSrc.quality && !video.src.includes("_"))))
      );
      if (active && label) label.innerText = src.new_description;
      return {
        label: src.new_description,
        value: src.quality,
        active,
        src,
      };
    });
    window.MikuPlayerUI?.setupLiveMenu({
      items,
      onSelect: (item) => {
        const src = item.src;
        if (!src) return;
        const newUrl = `/proxy/live/${window.current_vid}_${src.quality}`;
        if (label) label.innerText = src.new_description;
        if (window.liveManager) {
          console.log("[Player] Switching live quality to:", src.new_description);
          window.liveManager.destroy();
          window.liveManager = new LiveStreamManager(video, newUrl, list, label);
          window.liveManager.init();
        } else {
          video.src = newUrl;
          video.load();
          video.play().catch(() => {});
        }
      },
    });
  }
}

function setupVodQuality(video, list, label) {
  if (!window.supported_src) return;
  window.MikuPlayerUI?.setupVodMenu({
    sources: window.supported_src,
    isDash: Boolean(window.is_dash),
    isAudio: Boolean(window.is_audio),
    onSelectVideo: (src) => {
      if (window.is_dash) {
        window.dashManager?.setQuality(src);
        if (label) label.innerText = src.new_description;
        return;
      }
      const time = video.currentTime;
      const paused = video.paused;
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
  });
}

function toggleDanmaku() {
  if (!window.dm) return;
  if (window.dm_status) {
    window.dm.hide();
  } else {
    window.dm.show();
  }
  window.dm_status = !window.dm_status;
  window.MikuPlayerUI?.setDanmakuActive(window.dm_status);
}

function setupAutoNext(video) {
  const continueCb = document.getElementById("continue");
  const colContinueCb = document.getElementById("collection-continue");
  const syncAvSwitch = (checked) => {
    const avswitch = document.getElementById("avswitch");
    if (!avswitch) return;
    try {
      const u = new URL(avswitch.getAttribute("href"), window.location.origin);
      if (checked) u.searchParams.set("ato", "1");
      else u.searchParams.delete("ato");
      avswitch.setAttribute("href", u.pathname + u.search);
    } catch {
      /* ignore */
    }
  };
  if (continueCb && !continueCb.dataset.atoInit) {
    continueCb.checked = Boolean(window.ato);
    continueCb.dataset.atoInit = "1";
    continueCb.addEventListener("change", () => syncAvSwitch(continueCb.checked));
  }
  if (colContinueCb && !colContinueCb.dataset.atoInit) {
    colContinueCb.checked = Boolean(window.ato);
    colContinueCb.dataset.atoInit = "1";
    colContinueCb.addEventListener("change", () => syncAvSwitch(colContinueCb.checked));
  }
  video.addEventListener("ended", function () {
    const vid = String(window.current_vid || "");
    if (continueCb?.checked && window.idx + 1 < window.total_pages) {
      window.idx += 1;
      if (vid.startsWith("am")) {
        window.location.href = `/audio_list/${vid}:${window.idx}?ato=1`;
      } else {
        const suffix = window.is_audio ? "?listen=1&ato=1" : "?ato=1";
        window.location.href = `/video/${vid}:${window.idx}${suffix}`;
      }
      return;
    }
    if (colContinueCb?.checked && colContinueCb.dataset.nextBvid) {
      const suffix = window.is_audio ? "?listen=1&ato=1" : "?ato=1";
      window.location.href = `/video/${colContinueCb.dataset.nextBvid}${suffix}`;
    }
  });
}

window.initMikuPlayer = initMikuPlayer;
window.toggleDanmaku = toggleDanmaku;
document.addEventListener("DOMContentLoaded", initMikuPlayer);

/* @license-end */
