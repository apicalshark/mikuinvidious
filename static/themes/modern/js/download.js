/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

(function () {
  "use strict";

  var POLL_MS = 1000;
  var MAX_POLL_FAILURES = 8;
  var NONE_ID = "__none__";

  var sheet = null;
  var active = null;
  var activeForm = null;
  var rawOptions = null;

  var state = {
    videos: [],
    audios: [],
    progressive: [],
    selectedVideoId: "",
    selectedAudioId: "",
    selectedProgId: "",
  };

  function fmtBytes(n) {
    if (!n || n <= 0) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
    return (n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + units[i];
  }

  function fmtSpeed(bps) {
    if (!bps || bps <= 0) return "";
    return fmtBytes(bps) + "/s";
  }

  function fmtEta(totalBytes, doneBytes, speedBps) {
    if (!totalBytes || !speedBps || speedBps <= 0 || doneBytes >= totalBytes) return "";
    var secs = Math.max(0, Math.round((totalBytes - doneBytes) / speedBps));
    var hrs = Math.floor(secs / 3600);
    var mins = Math.floor((secs % 3600) / 60);
    var rem = secs % 60;
    if (hrs > 0) return hrs + "h " + mins + "m";
    if (mins > 0) return mins + "m " + rem + "s";
    return rem + "s";
  }

  function pinBestAudio() {
    try {
      if (typeof helpers !== "undefined" && helpers.storage) {
        var v = helpers.storage.get("best_audio");
        if (v === "0") return false;
        if (v === "1") return true;
      }
    } catch (e) {
      return true;
    }
    return !/(?:^|;\s*)best_audio=0(?:;|$)/.test(document.cookie || "");
  }

  function preferredCodec() {
    var m = (document.cookie || "").match(/(?:^|;\s*)preferred_codec=([^;]+)/);
    var cv = m ? String(m[1]).toLowerCase() : "";
    if (cv === "av1" || cv === "hevc" || cv === "avc" || cv === "auto") return cv;
    try {
      if (typeof helpers !== "undefined" && helpers.storage) {
        var v = String(helpers.storage.get("preferred_codec") || "").toLowerCase();
        if (v === "av1" || v === "hevc" || v === "avc" || v === "auto") return v;
      }
    } catch (e) {
      void e;
    }
    return "auto";
  }

  function videoCodecRank(codecLabel, pref) {
    var c = String(codecLabel || "").toUpperCase();
    if (pref === "hevc") {
      if (c === "HEVC") return 3;
      if (c === "AVC") return 2;
      if (c === "AV1") return 1;
      return 0;
    }
    if (pref === "avc") {
      if (c === "AVC") return 3;
      if (c === "HEVC") return 2;
      if (c === "AV1") return 1;
      return 0;
    }
    if (c === "AV1") return 3;
    if (c === "HEVC") return 2;
    if (c === "AVC") return 1;
    return 0;
  }

  function cleanQualityLabel(text) {
    if (!text) return "";
    return String(text)
      .replace(
        /\s*(?:超高清|超清|高清|清晰|流畅|流暢|标清|標清|极速|極速|高码率|高碼率|高帧率|高幀率)/g,
        ""
      )
      .replace(/(\d+)P\b/g, "$1p")
      .trim();
  }

  function buildNormalizedOptions(data) {
    var videosRaw = (data && Array.isArray(data.videos) ? data.videos : []).slice();
    var audiosRaw = (data && Array.isArray(data.audios) ? data.audios : []).slice();
    var progRaw = (data && Array.isArray(data.progressive) ? data.progressive : []).slice();
    var prefCodec = preferredCodec();

    var videos = videosRaw.map(function (v, idx) {
      var resLabel =
        v.height >= 4320
          ? "8K"
          : v.height >= 2160
            ? "4K"
            : v.height > 0
              ? v.height + "p"
              : cleanQualityLabel(v.description) || I18n.t("Video");
      var fpsLabel = v.fps > 30 ? " " + v.fps + "fps" : "";
      var kbps = v.bandwidth > 0 ? Math.round(v.bandwidth / 1000) + " kbps" : "";
      var codecLabel = v.codec_label || "VIDEO";
      var sizeBytes = v.size_bytes > 0 ? v.size_bytes : 0;
      return {
        id: "video-" + v.id + "-" + v.codecid + "-" + idx,
        qual: v.id,
        vCid: v.codecid,
        height: v.height || 0,
        fps: v.fps || 0,
        bandwidth: v.bandwidth || 0,
        codecLabel: codecLabel,
        label: resLabel + fpsLabel,
        detail: [codecLabel, kbps].filter(Boolean).join(" · "),
        sizeBytes: sizeBytes,
        sizeText: sizeBytes > 0 ? "~" + fmtBytes(sizeBytes) : I18n.t("Unknown size"),
        recommended: false,
      };
    });

    videos.sort(function (a, b) {
      if (b.height !== a.height) return b.height - a.height;
      if (b.qual !== a.qual) return b.qual - a.qual;
      if (b.fps !== a.fps) return b.fps - a.fps;
      var rDiff = videoCodecRank(b.codecLabel, prefCodec) - videoCodecRank(a.codecLabel, prefCodec);
      if (rDiff !== 0) return rDiff;
      return b.bandwidth - a.bandwidth;
    });

    if (videos.length > 0) {
      var recIdx = videos.findIndex(function (item) {
        return item.height === 1080;
      });
      if (recIdx < 0) {
        recIdx = videos.findIndex(function (item) {
          return item.height === 720;
        });
      }
      if (recIdx < 0) {
        recIdx = videos.findIndex(function (item) {
          return item.height < 1080;
        });
      }
      if (recIdx < 0) recIdx = videos.length - 1;
      videos[recIdx].recommended = true;
    }

    var audios = audiosRaw.map(function (a, idx) {
      var kbpsNum = a.bandwidth > 0 ? Math.round(a.bandwidth / 1000) : 0;
      var container = String(a.container || "m4a").toLowerCase();
      var codec = a.codec_label || "AAC";
      var sizeBytes = a.size_bytes > 0 ? a.size_bytes : 0;
      var label = (kbpsNum > 0 ? kbpsNum + " kbps " : "") + codec;
      return {
        id: "audio-" + a.id + "-" + a.codecid + "-" + idx,
        aQn: a.id,
        aCid: a.codecid,
        bitrate: kbpsNum,
        codecLabel: codec,
        container: container,
        label: label,
        detail: [a.codecs || codec, container.toUpperCase()].filter(Boolean).join(" · "),
        sizeBytes: sizeBytes,
        sizeText: sizeBytes > 0 ? "~" + fmtBytes(sizeBytes) : I18n.t("Unknown size"),
        recommended: false,
      };
    });

    if (audios.length > 0) {
      var recAudioIdx = audios.findIndex(function (item) {
        return item.bitrate >= 160 && item.bitrate < 256;
      });
      if (recAudioIdx < 0) recAudioIdx = 0;
      audios[recAudioIdx].recommended = true;
    }

    var progressive = progRaw.map(function (p, idx) {
      var ext = p.ext ? p.ext.replace(".", "").toLowerCase() : "mp4";
      return {
        id: "prog-" + p.quality + "-" + idx,
        qual: p.quality,
        container: ext,
        label: cleanQualityLabel(p.new_description) || I18n.t("Video"),
        detail: ext.toUpperCase(),
        sizeBytes: 0,
        sizeText: I18n.t("Unknown size"),
        recommended: idx === 0,
      };
    });

    return {
      videos: videos,
      audios: audios,
      progressive: progressive,
    };
  }

  function pickDefaultVideoId(videos, defaultAudioOnly) {
    if (!videos.length) return "";
    if (defaultAudioOnly) return NONE_ID;
    var rec = videos.find(function (v) {
      return v.recommended;
    });
    return (rec || videos[0]).id;
  }

  function pickDefaultAudioId(audios) {
    if (!audios.length) return "";
    if (pinBestAudio()) return audios[0].id;
    var rec = audios.find(function (a) {
      return a.recommended;
    });
    return (rec || audios[0]).id;
  }

  function buildSheet() {
    var backdrop = document.createElement("div");
    backdrop.id = "miku-dl-backdrop";
    backdrop.className =
      "fixed inset-0 z-[100] hidden items-center justify-center bg-black/60 backdrop-blur-xs p-4";

    var dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("tabindex", "-1");
    dialog.setAttribute("aria-label", I18n.t("Download"));
    dialog.className =
      "w-full max-w-md rounded-3xl bg-surface-container-low text-on-surface p-5 md:p-6 shadow-2xl";

    var header = document.createElement("div");
    header.className = "flex items-start justify-between gap-4 mb-4";

    var titleWrap = document.createElement("div");
    titleWrap.className = "min-w-0";
    var titleEl = document.createElement("h3");
    titleEl.className = "text-base font-bold flex items-center gap-2 text-on-surface";
    var titleIcon = document.createElement("i");
    titleIcon.className = "icon ion-md-download text-primary";
    var titleText = document.createElement("span");
    titleText.textContent = I18n.t("Download");
    titleEl.append(titleIcon, titleText);

    var subtitleEl = document.createElement("p");
    subtitleEl.className = "mt-0.5 text-xs text-on-surface-variant truncate";
    titleWrap.append(titleEl, subtitleEl);

    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", I18n.t("Close"));
    closeBtn.className =
      "rounded-full px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors shrink-0";
    closeBtn.textContent = I18n.t("Close");

    header.append(titleWrap, closeBtn);

    var pickerPane = document.createElement("div");
    pickerPane.className = "space-y-4";

    var fieldsWrap = document.createElement("div");
    fieldsWrap.className = "space-y-3";

    function createDropdownField(id, iconClass, labelText) {
      var wrap = document.createElement("div");
      wrap.className = "relative space-y-1.5";
      wrap.dataset.dlDropdown = "1";

      var head = document.createElement("div");
      head.className = "flex items-center justify-between gap-2 px-1";
      var label = document.createElement("label");
      label.setAttribute("for", id);
      label.className = "flex items-center gap-1.5 text-xs font-semibold text-on-surface";
      var headIcon = document.createElement("i");
      headIcon.className = "icon " + iconClass + " text-primary";
      var headLabel = document.createElement("span");
      headLabel.textContent = labelText;
      label.append(headIcon, headLabel);

      var countBadge = document.createElement("span");
      countBadge.className = "text-[11px] text-on-surface-variant tabular-nums";
      head.append(label, countBadge);

      var trigger = document.createElement("button");
      trigger.type = "button";
      trigger.id = id;
      trigger.setAttribute("aria-haspopup", "listbox");
      trigger.setAttribute("aria-expanded", "false");
      trigger.className =
        "w-full flex items-center justify-between gap-3 rounded-2xl bg-surface-container-high hover:bg-surface-container-highest text-on-surface px-4 py-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary cursor-pointer";

      var textCol = document.createElement("div");
      textCol.className = "min-w-0 flex-1";
      var primaryText = document.createElement("div");
      primaryText.className = "text-sm font-semibold truncate";
      var subText = document.createElement("div");
      subText.className = "text-xs text-on-surface-variant truncate hidden";
      textCol.append(primaryText, subText);

      var chevron = document.createElement("i");
      chevron.className =
        "icon ion-ios-arrow-down text-on-surface-variant transition-transform duration-200 shrink-0";
      trigger.append(textCol, chevron);

      var menu = document.createElement("div");
      menu.setAttribute("role", "listbox");
      menu.className =
        "hidden absolute left-0 right-0 top-full mt-2 z-30 rounded-2xl bg-surface-container-high p-1.5 shadow-xl max-h-60 overflow-y-auto custom-scrollbar space-y-0.5";

      wrap.append(head, trigger, menu);
      return {
        wrap: wrap,
        headLabel: headLabel,
        count: countBadge,
        trigger: trigger,
        primaryText: primaryText,
        subText: subText,
        chevron: chevron,
        menu: menu,
      };
    }

    var videoField = createDropdownField(
      "miku-dl-video-select",
      "ion-md-videocam",
      I18n.t("Video track")
    );
    var audioField = createDropdownField(
      "miku-dl-audio-select",
      "ion-md-musical-notes",
      I18n.t("Audio track")
    );
    fieldsWrap.append(videoField.wrap, audioField.wrap);

    var emptyMsg = document.createElement("p");
    emptyMsg.className = "hidden py-6 text-center text-sm text-on-surface-variant";
    emptyMsg.textContent = I18n.t("No downloadable formats available");

    var summaryBox = document.createElement("div");
    summaryBox.className = "rounded-2xl bg-surface-container px-4 py-3 space-y-3";

    var summaryRow = document.createElement("div");
    summaryRow.className = "flex items-center justify-between gap-3";
    var summaryText = document.createElement("p");
    summaryText.className = "text-xs font-medium text-on-surface min-w-0 truncate";
    var containerBadge = document.createElement("span");
    containerBadge.className =
      "rounded-full bg-primary/15 px-2.5 py-0.5 text-[10px] font-bold uppercase text-primary shrink-0";
    summaryRow.append(summaryText, containerBadge);

    var startBtn = document.createElement("button");
    startBtn.type = "button";
    startBtn.className = "md3-button md3-button-filled w-full";
    startBtn.textContent = I18n.t("Start download");

    summaryBox.append(summaryRow, startBtn);
    pickerPane.append(fieldsWrap, emptyMsg, summaryBox);

    var progressPane = document.createElement("div");
    progressPane.className = "hidden space-y-4";

    var selectedBadge = document.createElement("p");
    selectedBadge.className = "text-xs font-medium text-on-surface-variant truncate";

    var statusRow = document.createElement("div");
    statusRow.className = "flex items-center justify-between gap-3";
    var statusEl = document.createElement("p");
    statusEl.className = "text-sm font-medium text-on-surface";
    var percentEl = document.createElement("span");
    percentEl.className = "text-sm font-bold text-primary tabular-nums hidden";
    statusRow.append(statusEl, percentEl);

    var barTrack = document.createElement("div");
    barTrack.className = "h-2.5 rounded-full bg-surface-container-highest overflow-hidden";
    var barEl = document.createElement("div");
    barEl.className = "h-full w-0 rounded-full bg-primary transition-[width] duration-300";
    barTrack.appendChild(barEl);

    var transferEl = document.createElement("p");
    transferEl.className = "text-xs font-mono text-on-surface-variant hidden";

    var stepsGrid = document.createElement("div");
    stepsGrid.className = "grid grid-cols-3 gap-2 pt-1";
    var stepItems = [I18n.t("Prepare"), I18n.t("Download"), I18n.t("Finalize")].map(
      function (stepLabel) {
        var row = document.createElement("div");
        row.className = "flex items-center gap-2 text-xs text-on-surface-variant";
        var dot = document.createElement("span");
        dot.className = "size-2 rounded-full bg-surface-container-highest shrink-0";
        var text = document.createElement("span");
        text.textContent = stepLabel;
        row.append(dot, text);
        stepsGrid.appendChild(row);
        return { row: row, dot: dot };
      }
    );

    var retryEl = document.createElement("a");
    retryEl.href = "#";
    retryEl.className = "hidden block text-sm text-primary underline";
    retryEl.textContent = I18n.t(
      "If the download does not start automatically, click here to retry"
    );

    var actionRow = document.createElement("div");
    actionRow.className = "flex justify-end gap-2 pt-2";
    var backBtn = document.createElement("button");
    backBtn.type = "button";
    backBtn.className = "md3-button md3-button-tonal hidden";
    backBtn.textContent = I18n.t("Back");
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "md3-button md3-button-tonal";
    cancelBtn.textContent = I18n.t("Cancel");
    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "md3-button md3-button-filled hidden";
    okBtn.textContent = I18n.t("Close");
    actionRow.append(backBtn, cancelBtn, okBtn);

    progressPane.append(
      selectedBadge,
      statusRow,
      barTrack,
      transferEl,
      stepsGrid,
      retryEl,
      actionRow
    );

    dialog.append(header, pickerPane, progressPane);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    var refs = {
      backdrop: backdrop,
      dialog: dialog,
      titleText: titleText,
      subtitle: subtitleEl,
      close: closeBtn,
      pickerPane: pickerPane,
      fieldsWrap: fieldsWrap,
      videoField: videoField,
      audioField: audioField,
      emptyMsg: emptyMsg,
      summaryBox: summaryBox,
      summaryText: summaryText,
      containerBadge: containerBadge,
      startBtn: startBtn,
      progressPane: progressPane,
      selectedBadge: selectedBadge,
      status: statusEl,
      percent: percentEl,
      bar: barEl,
      transfer: transferEl,
      stepsGrid: stepsGrid,
      stepItems: stepItems,
      retry: retryEl,
      back: backBtn,
      cancel: cancelBtn,
      ok: okBtn,
      previousFocus: null,
    };

    closeBtn.addEventListener("click", onCloseButton);
    okBtn.addEventListener("click", hideDialog);
    cancelBtn.addEventListener("click", onCancelButton);
    backBtn.addEventListener("click", function () {
      showPickerPane();
    });
    startBtn.addEventListener("click", function () {
      startSelectedDownload();
    });
    videoField.trigger.addEventListener("click", function (ev) {
      ev.stopPropagation();
      toggleDropdownField(videoField);
    });
    audioField.trigger.addEventListener("click", function (ev) {
      ev.stopPropagation();
      toggleDropdownField(audioField);
    });
    dialog.addEventListener("click", function (ev) {
      if (!ev.target.closest("[data-dl-dropdown]")) {
        closeAllDropdowns();
      }
    });
    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) {
        closeAllDropdowns();
        onCloseButton();
      }
    });
    backdrop.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        if (hasOpenDropdown()) {
          event.preventDefault();
          closeAllDropdowns();
          return;
        }
        event.preventDefault();
        onCloseButton();
        return;
      }
      if (event.key !== "Tab") return;

      var controls = Array.prototype.filter.call(
        refs.dialog.querySelectorAll(
          "a[href], button:not([disabled]), input:not([disabled]), " +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ),
        function (control) {
          return !control.closest(".hidden") && control.offsetParent !== null;
        }
      );
      if (!controls.length) {
        event.preventDefault();
        refs.dialog.focus();
        return;
      }
      var first = controls[0];
      var last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    return refs;
  }

  function ensureSheet() {
    if (!sheet) sheet = buildSheet();
    return sheet;
  }

  function setDropdownOpen(field, open) {
    field.menu.classList.toggle("hidden", !open);
    field.trigger.setAttribute("aria-expanded", open ? "true" : "false");
    field.chevron.classList.toggle("rotate-180", open);
  }

  function closeAllDropdowns(exceptField) {
    if (!sheet) return;
    [sheet.videoField, sheet.audioField].forEach(function (field) {
      if (field && field !== exceptField) {
        setDropdownOpen(field, false);
      }
    });
  }

  function hasOpenDropdown() {
    if (!sheet) return false;
    return (
      !sheet.videoField.menu.classList.contains("hidden") ||
      !sheet.audioField.menu.classList.contains("hidden")
    );
  }

  function toggleDropdownField(field) {
    var opening = field.menu.classList.contains("hidden");
    closeAllDropdowns(field);
    setDropdownOpen(field, opening);
  }

  function updateSummary() {
    var d = ensureSheet();
    var hasDash = state.videos.length > 0 || state.audios.length > 0;
    if (hasDash) {
      var pickedVideo =
        state.selectedVideoId && state.selectedVideoId !== NONE_ID
          ? state.videos.find(function (v) {
              return v.id === state.selectedVideoId;
            })
          : null;
      var pickedAudio =
        state.selectedAudioId && state.selectedAudioId !== NONE_ID
          ? state.audios.find(function (a) {
              return a.id === state.selectedAudioId;
            })
          : null;

      if (!pickedVideo && !pickedAudio) {
        d.summaryText.textContent = I18n.t("Select at least a video or audio track");
        d.containerBadge.classList.add("hidden");
        d.startBtn.disabled = true;
      } else if (pickedVideo && pickedAudio) {
        var container = pickedAudio.container === "flac" ? "MKV" : "MP4";
        var totalBytes =
          pickedVideo.sizeBytes > 0 && pickedAudio.sizeBytes > 0
            ? pickedVideo.sizeBytes + pickedAudio.sizeBytes
            : 0;
        d.summaryText.textContent =
          pickedVideo.label +
          " " +
          pickedVideo.codecLabel +
          " + " +
          pickedAudio.label +
          (totalBytes > 0 ? " · ~" + fmtBytes(totalBytes) : "");
        d.containerBadge.textContent = container;
        d.containerBadge.classList.remove("hidden");
        d.startBtn.disabled = false;
      } else if (pickedAudio) {
        var audioExt = (pickedAudio.container || "m4a").toUpperCase();
        d.summaryText.textContent =
          I18n.t("Audio only") +
          " · " +
          pickedAudio.label +
          (pickedAudio.sizeBytes > 0 ? " · ~" + fmtBytes(pickedAudio.sizeBytes) : "");
        d.containerBadge.textContent = audioExt;
        d.containerBadge.classList.remove("hidden");
        d.startBtn.disabled = false;
      } else {
        d.summaryText.textContent =
          I18n.t("Video only (silent)") +
          " · " +
          pickedVideo.label +
          " " +
          pickedVideo.codecLabel +
          (pickedVideo.sizeBytes > 0 ? " · ~" + fmtBytes(pickedVideo.sizeBytes) : "");
        d.containerBadge.textContent = "MP4";
        d.containerBadge.classList.remove("hidden");
        d.startBtn.disabled = false;
      }
      return;
    }

    var pickedProg = state.progressive.find(function (p) {
      return p.id === state.selectedProgId;
    });
    if (pickedProg) {
      d.summaryText.textContent = pickedProg.label;
      d.containerBadge.textContent = (pickedProg.container || "mp4").toUpperCase();
      d.containerBadge.classList.remove("hidden");
      d.startBtn.disabled = false;
    } else {
      d.containerBadge.classList.add("hidden");
      d.startBtn.disabled = true;
    }
  }

  function renderDropdownOptions(field, items, selectedValue, onSelect) {
    var selectedItem =
      items.find(function (item) {
        return item.value === selectedValue;
      }) || items[0];

    if (selectedItem) {
      field.primaryText.textContent = selectedItem.label;
      if (selectedItem.subtext) {
        field.subText.textContent = selectedItem.subtext;
        field.subText.classList.remove("hidden");
      } else {
        field.subText.textContent = "";
        field.subText.classList.add("hidden");
      }
    }

    field.menu.innerHTML = "";
    items.forEach(function (item) {
      var activeItem = selectedItem && item.value === selectedItem.value;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", activeItem ? "true" : "false");
      btn.className =
        "w-full flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition-colors cursor-pointer " +
        (activeItem
          ? "bg-primary/15 text-primary"
          : "text-on-surface hover:bg-surface-container-highest");

      var left = document.createElement("div");
      left.className = "min-w-0 flex-1";
      var top = document.createElement("div");
      top.className = "flex items-center gap-2";
      var labelSpan = document.createElement("span");
      labelSpan.className = "text-xs font-semibold truncate";
      labelSpan.textContent = item.label;
      top.appendChild(labelSpan);

      if (item.recommended) {
        var badge = document.createElement("span");
        badge.className =
          "rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-semibold text-primary shrink-0";
        badge.textContent = I18n.t("Recommended");
        top.appendChild(badge);
      }
      left.appendChild(top);

      if (item.subtext) {
        var sub = document.createElement("div");
        sub.className =
          "mt-0.5 text-[11px] truncate " +
          (activeItem ? "text-primary/80" : "text-on-surface-variant");
        sub.textContent = item.subtext;
        left.appendChild(sub);
      }

      btn.appendChild(left);
      if (activeItem) {
        var check = document.createElement("i");
        check.className = "icon ion-md-checkmark text-primary shrink-0";
        btn.appendChild(check);
      }

      btn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        closeAllDropdowns();
        onSelect(item.value);
      });
      field.menu.appendChild(btn);
    });
  }

  function renderPicker() {
    var d = ensureSheet();
    closeAllDropdowns();
    var hasVideos = state.videos.length > 0;
    var hasAudios = state.audios.length > 0;
    var hasDash = hasVideos || hasAudios;
    var hasProg = state.progressive.length > 0;

    if (!hasDash && !hasProg) {
      d.fieldsWrap.classList.add("hidden");
      d.summaryBox.classList.add("hidden");
      d.emptyMsg.classList.remove("hidden");
      d.startBtn.disabled = true;
      return;
    }

    d.emptyMsg.classList.add("hidden");
    d.fieldsWrap.classList.remove("hidden");
    d.summaryBox.classList.remove("hidden");

    if (hasDash) {
      d.videoField.wrap.classList.toggle("hidden", !hasVideos);
      d.audioField.wrap.classList.toggle("hidden", !hasAudios);
      d.videoField.headLabel.textContent = I18n.t("Video track");
      d.audioField.headLabel.textContent = I18n.t("Audio track");
      d.videoField.count.textContent = hasVideos ? String(state.videos.length) : "";
      d.audioField.count.textContent = hasAudios ? String(state.audios.length) : "";

      if (
        hasVideos &&
        state.selectedVideoId !== NONE_ID &&
        !state.videos.some(function (v) {
          return v.id === state.selectedVideoId;
        })
      ) {
        state.selectedVideoId = pickDefaultVideoId(
          state.videos,
          activeForm && activeForm.dataset.defaultMode === "audio"
        );
      } else if (!hasVideos) {
        state.selectedVideoId = "";
      }

      if (
        hasAudios &&
        state.selectedAudioId !== NONE_ID &&
        !state.audios.some(function (a) {
          return a.id === state.selectedAudioId;
        })
      ) {
        state.selectedAudioId = pickDefaultAudioId(state.audios);
      } else if (!hasAudios) {
        state.selectedAudioId = "";
      }

      if (hasVideos) {
        var videoItems = [];
        if (hasAudios) {
          videoItems.push({
            value: NONE_ID,
            label: I18n.t("No video (audio only)"),
            subtext: "",
            recommended: false,
          });
        }
        state.videos.forEach(function (opt) {
          videoItems.push({
            value: opt.id,
            label: opt.label,
            subtext: [opt.detail, opt.sizeText].filter(Boolean).join(" · "),
            recommended: opt.recommended,
          });
        });
        renderDropdownOptions(d.videoField, videoItems, state.selectedVideoId, function (val) {
          state.selectedVideoId = val;
          renderPicker();
        });
      }

      if (hasAudios) {
        var audioItems = [];
        if (hasVideos) {
          audioItems.push({
            value: NONE_ID,
            label: I18n.t("No audio (video only)"),
            subtext: "",
            recommended: false,
          });
        }
        state.audios.forEach(function (opt) {
          audioItems.push({
            value: opt.id,
            label: opt.label,
            subtext: [opt.detail, opt.sizeText].filter(Boolean).join(" · "),
            recommended: opt.recommended,
          });
        });
        renderDropdownOptions(d.audioField, audioItems, state.selectedAudioId, function (val) {
          state.selectedAudioId = val;
          renderPicker();
        });
      }

      updateSummary();
      return;
    }

    d.videoField.wrap.classList.remove("hidden");
    d.audioField.wrap.classList.add("hidden");
    d.videoField.headLabel.textContent = I18n.t("Video");
    d.videoField.count.textContent = String(state.progressive.length);

    if (
      !state.progressive.some(function (p) {
        return p.id === state.selectedProgId;
      })
    ) {
      state.selectedProgId = state.progressive[0].id;
    }

    var progItems = state.progressive.map(function (opt) {
      return {
        value: opt.id,
        label: opt.label,
        subtext: [opt.detail, opt.sizeText].filter(Boolean).join(" · "),
        recommended: opt.recommended,
      };
    });
    renderDropdownOptions(d.videoField, progItems, state.selectedProgId, function (val) {
      state.selectedProgId = val;
      renderPicker();
    });
    updateSummary();
  }

  function showPickerPane() {
    var d = ensureSheet();
    d.titleText.textContent = I18n.t("Download");
    d.pickerPane.classList.remove("hidden");
    d.progressPane.classList.add("hidden");
    renderPicker();
  }

  function showProgressPane(mode, selectedLabel) {
    var d = ensureSheet();
    closeAllDropdowns();
    d.titleText.textContent =
      mode === "audio" ? I18n.t("Download audio") : I18n.t("Download video");
    d.pickerPane.classList.add("hidden");
    d.progressPane.classList.remove("hidden");
    d.selectedBadge.textContent = selectedLabel || "";
    d.retry.classList.add("hidden");
    d.back.classList.add("hidden");
    d.cancel.classList.remove("hidden");
    d.ok.classList.add("hidden");
    d.transfer.classList.add("hidden");
    d.percent.classList.add("hidden");
    d.stepsGrid.classList.remove("hidden");
  }

  function applyOptionsData(sourceData, defaultAudioOnly) {
    var parsed = buildNormalizedOptions(sourceData);
    state.videos = parsed.videos;
    state.audios = parsed.audios;
    state.progressive = parsed.progressive;
    state.selectedVideoId = pickDefaultVideoId(parsed.videos, defaultAudioOnly);
    state.selectedAudioId = pickDefaultAudioId(parsed.audios);
    state.selectedProgId = parsed.progressive.length ? parsed.progressive[0].id : "";
  }

  function openSheet(form) {
    activeForm = form;
    var d = ensureSheet();
    d.subtitle.textContent = form.dataset.title || "";
    if (d.backdrop.classList.contains("hidden")) {
      d.previousFocus = document.activeElement;
    }

    var sourceData = rawOptions || window.download_options;
    applyOptionsData(sourceData, form.dataset.defaultMode === "audio");

    if (active && !active.terminal) {
      showProgressPane(active.mode, active.selectedLabel || "");
    } else {
      showPickerPane();
    }

    d.backdrop.classList.remove("hidden");
    d.backdrop.classList.add("flex");
    d.close.focus();
  }

  function hideDialog() {
    if (!sheet) return;
    var previousFocus = sheet.previousFocus;
    sheet.previousFocus = null;
    sheet.backdrop.classList.add("hidden");
    sheet.backdrop.classList.remove("flex");
    if (previousFocus && previousFocus.isConnected && previousFocus.focus) {
      previousFocus.focus();
    }
  }

  function setBar(percent) {
    var d = ensureSheet();
    if (percent === null || percent === undefined) {
      d.bar.style.width = "100%";
      d.bar.classList.add("animate-pulse");
    } else {
      d.bar.classList.remove("animate-pulse");
      d.bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
    }
  }

  function setStepIndex(idx) {
    var d = ensureSheet();
    d.stepItems.forEach(function (item, i) {
      var activeStep = i <= idx;
      item.dot.className =
        "size-2 rounded-full shrink-0 " +
        (activeStep ? "bg-primary" : "bg-surface-container-highest");
      item.row.className =
        "flex items-center gap-2 text-xs " +
        (activeStep ? "text-on-surface font-medium" : "text-on-surface-variant");
    });
  }

  function formatResolvedBadge(resolved) {
    if (!resolved || typeof resolved !== "object") return "";
    if (resolved.mode === "video" && (resolved.height || resolved.codec)) {
      var videoPart = [resolved.height ? resolved.height + "p" : "", resolved.codec]
        .filter(Boolean)
        .join(" ");
      var audioPart = [
        resolved.audio_bitrate ? resolved.audio_bitrate + " kbps" : "",
        resolved.audio_codec,
      ]
        .filter(Boolean)
        .join(" ");
      var combo = [videoPart, audioPart].filter(Boolean).join(" + ");
      if (resolved.container) combo += " · " + String(resolved.container).toUpperCase();
      return combo;
    }
    var parts = [];
    if (resolved.height) parts.push(resolved.height + "p");
    if (resolved.bitrate) parts.push(resolved.bitrate + " kbps");
    if (resolved.codec) parts.push(resolved.codec);
    var head = parts.join(" ");
    if (resolved.container) {
      head = head
        ? head + " · " + String(resolved.container).toUpperCase()
        : String(resolved.container).toUpperCase();
    }
    return head;
  }

  function render(st) {
    var d = ensureSheet();
    var label = "";
    var stepIdx = 0;

    if (st.state === "queued") {
      label = I18n.t("Queued... (server download slots are limited)");
      setBar(null);
      stepIdx = 0;
    } else if (st.state === "resolving") {
      label = I18n.t("Resolving stream info...");
      setBar(null);
      stepIdx = 0;
    } else if (st.state === "downloading") {
      stepIdx = 1;
      if (st.note) {
        label = st.note;
        setBar(st.percent !== null && st.percent !== undefined ? st.percent : null);
      } else if (st.percent !== null && st.percent !== undefined) {
        label = I18n.t("Downloading...");
        setBar(st.percent);
      } else {
        label = I18n.t("Downloading...");
        setBar(null);
      }
    } else if (st.state === "muxing") {
      stepIdx = 2;
      var activeMode = st.mode || (active && active.mode);
      if (activeMode === "audio") {
        label = I18n.t("Remuxing audio, please wait...");
      } else if (activeMode === "video_only") {
        label = I18n.t("Remuxing video, please wait...");
      } else {
        label = I18n.t("Muxing... (merging audio and video, please wait)");
      }
      setBar(null);
    } else if (st.state === "ready") {
      stepIdx = 2;
      label = I18n.t("Done, download started in browser");
      setBar(100);
    } else if (st.state === "cancelled") {
      label = I18n.t("Cancelled");
      setBar(0);
    } else if (st.state === "error") {
      label = st.error
        ? I18n.t("Download failed: %(error)s", { error: st.error })
        : I18n.t("Download failed");
      setBar(0);
    }

    d.status.textContent = label;
    setStepIndex(stepIdx);

    var showPct = st.state === "downloading" && st.percent !== null && st.percent !== undefined;
    d.percent.classList.toggle("hidden", !showPct);
    if (showPct) {
      d.percent.textContent = Math.round(st.percent) + "%";
    }

    if (st.state === "downloading" && st.done_bytes > 0) {
      var details = [
        fmtBytes(st.done_bytes) + (st.total_bytes > 0 ? " / " + fmtBytes(st.total_bytes) : ""),
      ];
      var speed = fmtSpeed(st.speed_bps);
      if (speed) details.push(speed);
      var eta = fmtEta(st.total_bytes, st.done_bytes, st.speed_bps);
      if (eta) details.push(eta);
      d.transfer.textContent = details.join(" · ");
      d.transfer.classList.remove("hidden");
    } else {
      d.transfer.classList.add("hidden");
    }

    var resolvedText = formatResolvedBadge(st.resolved);
    if (resolvedText) {
      d.selectedBadge.textContent = I18n.t("Selected: %(format)s", { format: resolvedText });
    }

    var terminal = st.state === "ready" || st.state === "error" || st.state === "cancelled";
    d.stepsGrid.classList.toggle("hidden", st.state === "error" || st.state === "cancelled");
    d.cancel.classList.toggle("hidden", terminal);
    d.ok.classList.toggle("hidden", !terminal);
    d.back.classList.toggle("hidden", !terminal);

    if (st.state === "ready" && active) {
      d.retry.href = active.fileUrl;
      d.retry.classList.remove("hidden");
    } else {
      d.retry.classList.add("hidden");
    }
  }

  function stopPolling() {
    if (active && active.timer) {
      clearTimeout(active.timer);
      active.timer = null;
    }
  }

  async function pollStatus() {
    if (!active || active.terminal) return;
    var current = active;
    var jobId = current.jobId;
    try {
      var resp = await fetch("/download/status/" + encodeURIComponent(jobId), {
        credentials: "same-origin",
      });
      if (active !== current || active.jobId !== jobId) return;
      if (resp.status === 404) {
        current.terminal = true;
        render({ state: "error", error: I18n.t("Task expired, please download again") });
        return;
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var st = await resp.json();
      if (active !== current || active.jobId !== jobId) return;
      current.failures = 0;
      render(st);
      if (st.state === "ready" || st.state === "error" || st.state === "cancelled") {
        current.terminal = true;
        if (st.state === "ready") triggerDirectDownload(current.fileUrl);
        return;
      }
    } catch (err) {
      if (active !== current || active.jobId !== jobId) return;
      current.failures += 1;
      if (current.failures >= MAX_POLL_FAILURES) {
        current.terminal = true;
        render({ state: "error", error: I18n.t("Lost connection to the server, please retry") });
        return;
      }
    }
    if (active !== current || active.jobId !== jobId) return;
    current.timer = setTimeout(pollStatus, POLL_MS);
  }

  function triggerDirectDownload(url) {
    if (!url) return;
    var a = document.createElement("a");
    a.href = url;
    a.setAttribute("download", "");
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      a.remove();
    }, 1000);
  }

  async function cancelActive(silent) {
    if (!active || active.terminal) return;
    var current = active;
    var jobId = current.jobId;
    var csrf = current.csrf;
    stopPolling();
    current.terminal = true;
    try {
      var body = new FormData();
      body.append("csrf_token", csrf);
      await fetch("/download/cancel/" + encodeURIComponent(jobId), {
        method: "POST",
        body: body,
        credentials: "same-origin",
      });
    } catch (err) {
      void err;
    }
    if (active !== current || active.jobId !== jobId) return;
    if (!silent) render({ state: "cancelled" });
  }

  function onCancelButton() {
    cancelActive(false);
  }

  function onCloseButton() {
    if (active && !active.terminal) {
      cancelActive(false);
    }
    hideDialog();
  }

  function onPageHide() {
    if (active && !active.terminal) {
      var body = new FormData();
      body.append("csrf_token", active.csrf);
      try {
        navigator.sendBeacon("/download/cancel/" + encodeURIComponent(active.jobId), body);
      } catch (err) {
        void err;
      }
    }
  }

  function buildSelectionPayload() {
    var hasDash = state.videos.length > 0 || state.audios.length > 0;
    if (hasDash) {
      var pickedVideo =
        state.selectedVideoId && state.selectedVideoId !== NONE_ID
          ? state.videos.find(function (v) {
              return v.id === state.selectedVideoId;
            })
          : null;
      var pickedAudio =
        state.selectedAudioId && state.selectedAudioId !== NONE_ID
          ? state.audios.find(function (a) {
              return a.id === state.selectedAudioId;
            })
          : null;

      if (!pickedVideo && !pickedAudio) return null;

      if (pickedVideo && pickedAudio) {
        var container = pickedAudio.container === "flac" ? "MKV" : "MP4";
        return {
          mode: "video",
          qual: pickedVideo.qual || 0,
          vCid: pickedVideo.vCid,
          aQn: pickedAudio.aQn,
          aCid: pickedAudio.aCid,
          label:
            pickedVideo.label +
            " " +
            pickedVideo.codecLabel +
            " + " +
            pickedAudio.label +
            " · " +
            container,
        };
      }
      if (pickedAudio) {
        return {
          mode: "audio",
          qual: pickedAudio.aQn || 0,
          vCid: null,
          aQn: pickedAudio.aQn,
          aCid: pickedAudio.aCid,
          label: pickedAudio.label + " · " + (pickedAudio.container || "m4a").toUpperCase(),
        };
      }
      return {
        mode: "video_only",
        qual: pickedVideo.qual || 0,
        vCid: pickedVideo.vCid,
        aQn: null,
        aCid: null,
        label: pickedVideo.label + " " + pickedVideo.codecLabel + " · MP4",
      };
    }

    var pickedProg = state.progressive.find(function (p) {
      return p.id === state.selectedProgId;
    });
    if (!pickedProg) return null;
    return {
      mode: "video",
      qual: pickedProg.qual || 0,
      vCid: null,
      aQn: null,
      aCid: null,
      label: pickedProg.label,
    };
  }

  async function startSelectedDownload() {
    if (!activeForm) return;
    var choice = buildSelectionPayload();
    if (!choice) return;

    if (active && !active.terminal) await cancelActive(true);

    var data = new FormData(activeForm);
    var csrf = data.get("csrf_token") || "";
    data.set("mode", choice.mode);
    data.set("qual", String(choice.qual || 0));
    data.delete("v_cid");
    data.delete("a_qn");
    data.delete("a_cid");
    if (choice.vCid !== null && choice.vCid !== undefined) {
      data.set("v_cid", String(choice.vCid));
    }
    if (choice.aQn !== null && choice.aQn !== undefined) {
      data.set("a_qn", String(choice.aQn));
    }
    if (choice.aCid !== null && choice.aCid !== undefined) {
      data.set("a_cid", String(choice.aCid));
    }

    var selectedBadge = I18n.t("Selected: %(format)s", { format: choice.label });
    showProgressPane(choice.mode, selectedBadge);
    var d = ensureSheet();
    d.status.textContent = I18n.t("Creating download task...");
    setBar(null);
    setStepIndex(0);

    var jobId;
    try {
      var resp = await fetch(activeForm.action, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });
      var payload = await resp.json().catch(function () {
        return {};
      });
      if (payload.direct_url) {
        triggerDirectDownload(payload.direct_url);
        active = { terminal: true, fileUrl: payload.direct_url };
        render({ state: "ready" });
        return;
      }
      if (!resp.ok || !payload.job_id) {
        throw new Error(payload.error || "HTTP " + resp.status);
      }
      jobId = payload.job_id;
    } catch (err) {
      render({ state: "error", error: String((err && err.message) || err) });
      active = { terminal: true };
      return;
    }

    active = {
      jobId: jobId,
      csrf: csrf,
      mode: choice.mode,
      selectedLabel: selectedBadge,
      timer: null,
      failures: 0,
      terminal: false,
      fileUrl: "/download/file/" + encodeURIComponent(jobId),
    };
    pollStatus();
  }

  window.MikuDownload = {
    setOptions: function (options) {
      rawOptions = options || null;
      if (sheet && !sheet.backdrop.classList.contains("hidden") && (!active || active.terminal)) {
        var parsed = buildNormalizedOptions(rawOptions);
        state.videos = parsed.videos;
        state.audios = parsed.audios;
        state.progressive = parsed.progressive;
        renderPicker();
      }
    },
    attach: function (form) {
      if (typeof form === "string") form = document.getElementById(form);
      if (!form || form.__mikuDlAttached) return;
      form.__mikuDlAttached = true;
      form.removeAttribute("target");
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (form.dataset.directUrl) {
          triggerDirectDownload(form.dataset.directUrl);
          return;
        }
        openSheet(form);
      });
      window.addEventListener("pagehide", onPageHide);
    },
  };
})();
