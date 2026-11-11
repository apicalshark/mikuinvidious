/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

"use strict";

function getCookie(name) {
  let value = "; " + document.cookie;
  let parts = value.split("; " + name + "=");
  if (parts.length === 2) return parts.pop().split(";").shift();
}

var toggle_theme = document.getElementById("toggle_theme");
var toggle_theme_mobile = document.getElementById("toggle_theme_mobile");

var STORAGE_KEY_THEME = "dark_mode";
var THEME_DARK = "dark";
var THEME_LIGHT = "light";

function handleThemeToggle(e) {
  if (e) e.preventDefault();
  const isDarkTheme = helpers.storage.get(STORAGE_KEY_THEME) === THEME_DARK;
  const newTheme = isDarkTheme ? THEME_LIGHT : THEME_DARK;
  setTheme(newTheme);
  helpers.storage.set(STORAGE_KEY_THEME, newTheme);
}

if (toggle_theme) toggle_theme.addEventListener("click", handleThemeToggle);
if (toggle_theme_mobile) toggle_theme_mobile.addEventListener("click", handleThemeToggle);

// Mobile search toggle
var mobile_search_btn = document.getElementById("mobile_search_btn");
var search_container = document.getElementById("search_container");
var searchbox = document.getElementById("searchbox");

if (mobile_search_btn && search_container) {
  mobile_search_btn.addEventListener("click", function () {
    search_container.classList.toggle("hidden");
    if (!search_container.classList.contains("hidden")) {
      searchbox.focus();
    }
  });
}

var toggle_opencc = document.getElementById("toggle_opencc");
if (toggle_opencc) {
  toggle_opencc.addEventListener("click", function () {
    const oldVal = helpers.storage.get("opencc") || getCookie("opencc");
    const newVal = oldVal === "1" ? "0" : "1";
    helpers.storage.set("opencc", newVal);
    const secureFlag = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      "opencc=" + newVal + "; path=/; max-age=" + 3600 * 24 * 30 + "; SameSite=Lax" + secureFlag;
    location.reload();
  });
}

var toggle_search_opencc = document.getElementById("toggle_search_opencc");
if (toggle_search_opencc) {
  toggle_search_opencc.addEventListener("click", function () {
    const oldVal = helpers.storage.get("search_opencc") || getCookie("search_opencc");
    const newVal = oldVal === "1" ? "0" : "1";
    helpers.storage.set("search_opencc", newVal);
    const secureFlag = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      "search_opencc=" +
      newVal +
      "; path=/; max-age=" +
      3600 * 24 * 30 +
      "; SameSite=Lax" +
      secureFlag;
    location.reload();
  });
}

var toggle_best_audio = document.getElementById("toggle_best_audio");
if (toggle_best_audio) {
  toggle_best_audio.addEventListener("click", function () {
    const oldVal = helpers.storage.get("best_audio") || getCookie("best_audio");
    const newVal = oldVal === "1" ? "0" : "1";
    helpers.storage.set("best_audio", newVal);
    const secureFlag = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      "best_audio=" +
      newVal +
      "; path=/; max-age=" +
      3600 * 24 * 30 +
      "; SameSite=Lax" +
      secureFlag;
    location.reload();
  });
}

var toggle_lock_video = document.getElementById("toggle_lock_video");
if (toggle_lock_video) {
  toggle_lock_video.addEventListener("click", function () {
    const oldVal = helpers.storage.get("lock_video") || getCookie("lock_video");
    const newVal = oldVal === "1" ? "0" : "1";
    helpers.storage.set("lock_video", newVal);
    const secureFlag = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      "lock_video=" +
      newVal +
      "; path=/; max-age=" +
      3600 * 24 * 30 +
      "; SameSite=Lax" +
      secureFlag;
    location.reload();
  });
}

/** @param {THEME_DARK|THEME_LIGHT} theme */
function setTheme(theme) {
  const iconClass = theme === THEME_DARK ? "icon ion-ios-sunny" : "icon ion-ios-moon";
  if (toggle_theme) toggle_theme.children[0].className = iconClass;
  if (toggle_theme_mobile) toggle_theme_mobile.children[0].className = iconClass;

  if (theme === THEME_DARK) {
    document.documentElement.classList.add("dark");
    document.documentElement.classList.remove("light");
  } else {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  }
  if (window.M3Theme) {
    window.M3Theme.applyTokens(null, theme === THEME_DARK);
  }
}

// Handles theme change event caused by other tab
addEventListener("storage", function (e) {
  if (e.key === STORAGE_KEY_THEME) setTheme(helpers.storage.get(STORAGE_KEY_THEME));
});

var _openccPromise = null;
function ensureOpenCC() {
  if (window.OpenCC) return Promise.resolve(window.OpenCC);
  if (_openccPromise) return _openccPromise;
  _openccPromise = new Promise(function (resolve, reject) {
    var s = document.createElement("script");
    var v =
      (typeof window !== "undefined" && window.asset_version) ||
      document.querySelector('meta[name="asset-version"]')?.getAttribute("content") ||
      "";
    if (!v) {
      var script = document.querySelector('script[src*="themes.js"], script[src*="?v="]');
      if (script && script.src) {
        var match = script.src.match(/[?&]v=([^&#]+)/);
        if (match) v = match[1];
      }
    }
    s.src = "/static/opencc-js/opencc.js" + (v ? "?v=" + encodeURIComponent(v) : "");
    var nonce = document.querySelector('meta[name="csp-nonce"]')?.getAttribute("content") || "";
    if (nonce) s.nonce = nonce;
    s.defer = true;
    s.onload = function () {
      resolve(window.OpenCC);
    };
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return _openccPromise;
}

// Set preferences on page load
function initPreferences() {
  // Client-side theme: localStorage wins. When nothing is stored yet, adopt
  // the pre-paint default from <head> (OS preference) and persist it so the
  // toggle and cross-tab sync have a defined state.
  let storedTheme = helpers.storage.get(STORAGE_KEY_THEME);
  if (storedTheme !== THEME_DARK && storedTheme !== THEME_LIGHT) {
    storedTheme = document.documentElement.classList.contains("dark") ? THEME_DARK : THEME_LIGHT;
    helpers.storage.set(STORAGE_KEY_THEME, storedTheme);
  }
  setTheme(storedTheme);
  if (window.M3Theme) {
    window.M3Theme.applyTokens(null, storedTheme === THEME_DARK);
  }

  const openccPref =
    (typeof helpers !== "undefined" && helpers.storage.get("opencc")) || getCookie("opencc");

  if (openccPref === "1") {
    ensureOpenCC()
      .then(function () {
        if (!window.OpenCC) return;
        initOpenccConvert();
      })
      .catch(function () {});
  }

  function initOpenccConvert() {
    const converter = OpenCC.Converter({ from: "cn", to: "twp" });

    const convertNode = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.nodeValue.trim().length > 0) {
          if (node.originalString === undefined) node.originalString = node.nodeValue;
          node.nodeValue = converter(node.originalString);
        }
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (
          node.classList.contains("ignore-opencc") ||
          node.tagName === "SCRIPT" ||
          node.tagName === "STYLE"
        )
          return;

        if (node.tagName === "INPUT" && (node.type === "button" || node.type === "submit")) {
          if (node.originalValue === undefined) node.originalValue = node.value;
          node.value = converter(node.originalValue);
        }

        if (node.placeholder) {
          if (node.originalPlaceholder === undefined) node.originalPlaceholder = node.placeholder;
          node.placeholder = converter(node.originalPlaceholder);
        }

        if (node.title) {
          if (node.originalTitle === undefined) node.originalTitle = node.title;
          node.title = converter(node.originalTitle);
        }

        for (let child of node.childNodes) {
          convertNode(child);
        }
      }
    };

    convertNode(document.body);
    document.documentElement.lang = "zh-Hant";

    // Observe dynamic changes
    const observer = new MutationObserver(function (mutations) {
      for (let mutation of mutations) {
        for (let node of mutation.addedNodes) {
          convertNode(node);
        }
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  // Update pref page buttons if they exist
  const btnOpencc = document.getElementById("toggle_opencc");
  if (btnOpencc) {
    if (openccPref === "1") {
      btnOpencc.classList.add("is-on");
      btnOpencc.innerText = I18n.t("On");
    } else {
      btnOpencc.classList.remove("is-on");
      btnOpencc.innerText = I18n.t("Off");
    }
  }

  const searchOpenccPref = helpers.storage.get("search_opencc") || getCookie("search_opencc");
  const btnSearchOpencc = document.getElementById("toggle_search_opencc");
  if (btnSearchOpencc) {
    if (searchOpenccPref === "1") {
      btnSearchOpencc.classList.add("is-on");
      btnSearchOpencc.innerText = I18n.t("On");
    } else {
      btnSearchOpencc.classList.remove("is-on");
      btnSearchOpencc.innerText = I18n.t("Off");
    }
  }

  const bestAudioPref = helpers.storage.get("best_audio") || getCookie("best_audio");
  const btnBestAudio = document.getElementById("toggle_best_audio");
  if (btnBestAudio) {
    // Default ON: only an explicit "0" shows Off.
    if (bestAudioPref === "0") {
      btnBestAudio.classList.remove("is-on");
      btnBestAudio.innerText = I18n.t("Off");
    } else {
      btnBestAudio.classList.add("is-on");
      btnBestAudio.innerText = I18n.t("On");
    }
  }

  const lockVideoPref = helpers.storage.get("lock_video") || getCookie("lock_video");
  const btnLockVideo = document.getElementById("toggle_lock_video");
  if (btnLockVideo) {
    // Default OFF: only an explicit "1" shows On.
    if (lockVideoPref === "1") {
      btnLockVideo.classList.add("is-on");
      btnLockVideo.innerText = I18n.t("On");
    } else {
      btnLockVideo.classList.remove("is-on");
      btnLockVideo.innerText = I18n.t("Off");
    }
  }

  // Material You Palette Picker
  const seedPicker = document.getElementById("m3-seed-picker");
  if (seedPicker && window.M3Theme) {
    const currentSeed = window.M3Theme.getStoredSeed();
    const swatches = seedPicker.querySelectorAll(".m3-seed-swatch");
    const customColorInput = document.getElementById("m3-custom-color");

    function updateActiveSwatch(seed) {
      const isDef = !seed || (window.M3Theme && window.M3Theme.isDefault(seed));
      swatches.forEach((sw) => {
        const swSeed = sw.dataset.seed;
        const match = isDef
          ? window.M3Theme && window.M3Theme.isDefault(swSeed)
          : swSeed.toLowerCase() === seed.toLowerCase();
        if (match) {
          sw.classList.add("active", "ring-2", "ring-offset-2", "ring-primary");
        } else {
          sw.classList.remove("active", "ring-2", "ring-offset-2", "ring-primary");
        }
      });
      if (customColorInput && !isDef) {
        customColorInput.value = seed;
      }
    }

    updateActiveSwatch(currentSeed);

    swatches.forEach((sw) => {
      sw.addEventListener("click", () => {
        const seed = sw.dataset.seed;
        window.M3Theme.setSeedColor(seed);
        updateActiveSwatch(seed);
      });
    });

    if (customColorInput) {
      customColorInput.addEventListener("input", (e) => {
        const seed = e.target.value;
        window.M3Theme.setSeedColor(seed);
        updateActiveSwatch(seed);
      });
    }
  }

  if (searchOpenccPref === "1") {
    const searchForm = document.querySelector('form[action="/search"]');
    if (searchForm) {
      // Kick off the lazy load immediately, but register the handler NOW so
      // a submit during the load still converts instead of bypassing.
      var openccReady = ensureOpenCC().catch(function () {
        return null;
      });
      var s2sConverter = null;
      openccReady.then(function (OC) {
        if (OC) s2sConverter = OC.Converter({ from: "tw", to: "cn" });
      });
      var resubmitting = false;
      var submitHeld = false;
      searchForm.addEventListener("submit", function (e) {
        if (resubmitting) return; // second pass after deferred conversion
        if (s2sConverter) {
          var sb = document.getElementById("searchbox");
          sb.value = s2sConverter(sb.value);
          return;
        }
        // Library still loading: hold this submit, convert on arrival, then
        // resubmit once. Extra submits while held are dropped (one navigation).
        e.preventDefault();
        if (submitHeld) return;
        submitHeld = true;
        openccReady.then(function () {
          resubmitting = true;
          try {
            if (s2sConverter) {
              var sb2 = document.getElementById("searchbox");
              sb2.value = s2sConverter(sb2.value);
            }
            if (typeof searchForm.requestSubmit === "function") {
              searchForm.requestSubmit();
            } else {
              searchForm.submit();
            }
          } finally {
            resubmitting = false;
            submitHeld = false;
          }
        });
      });
    }
  }
}

if (document.readyState === "loading") {
  addEventListener("DOMContentLoaded", initPreferences);
} else {
  initPreferences();
}

// Navbar language switcher: picking a language POSTs to /set_lang (CSRF-protected)
// and reloads. Works with native HTML Popover API.
async function setLanguage(lang) {
  const secureFlag = location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    "lang=" +
    encodeURIComponent(lang) +
    "; path=/; max-age=" +
    3600 * 24 * 30 +
    "; SameSite=Lax" +
    secureFlag;

  const cleanUrl = () => {
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("lang");
      return url.toString();
    } catch (e) {
      return window.location.href;
    }
  };

  const reloadClean = () => {
    const target = cleanUrl();
    if (target !== window.location.href) {
      window.location.replace(target);
    } else {
      window.location.reload();
    }
  };

  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
    document.querySelector('input[name="csrf_token"]')?.value;
  if (!csrfToken) {
    reloadClean();
    return;
  }
  // Send POST /set_lang to keep server session/cookies in sync
  helpers.xhr(
    "POST",
    "/set_lang",
    {
      payload: "lang=" + encodeURIComponent(lang) + "&csrf_token=" + encodeURIComponent(csrfToken),
    },
    {
      on200: reloadClean,
      onNon200: reloadClean,
      onError: reloadClean,
      onTimeout: reloadClean,
    }
  );
}

document.querySelectorAll(".lang-option").forEach((opt) => {
  opt.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = opt.closest("[popover]");
    if (menu && menu.hidePopover) {
      try {
        menu.hidePopover();
      } catch (err) {}
    }
    setLanguage(opt.dataset.lang);
  });
});

// Popover positioning fallback for browsers without CSS Anchor Positioning
function positionPopover(popover, trigger) {
  const rect = trigger.getBoundingClientRect();
  popover.style.margin = "0";
  popover.style.position = "fixed";
  popover.style.top = `${rect.bottom + 8}px`;

  // Align right edge of popover with right edge of trigger button
  const rightOffset = Math.max(8, window.innerWidth - rect.right);
  popover.style.right = `${rightOffset}px`;
  popover.style.left = "auto";

  requestAnimationFrame(() => {
    const popRect = popover.getBoundingClientRect();
    if (popRect.bottom > window.innerHeight - 8) {
      popover.style.top = `${Math.max(8, rect.top - popRect.height - 8)}px`;
    }
    if (popRect.left < 8) {
      popover.style.left = "8px";
      popover.style.right = "auto";
    }
  });
}

document.querySelectorAll("[popover]").forEach((popover) => {
  const id = popover.id;
  if (!id) return;

  const updatePos = () => {
    const triggers = document.querySelectorAll(`[popovertarget="${id}"]`);
    let trigger = triggers[0];
    for (const t of triggers) {
      if (t.offsetParent !== null || t.offsetWidth > 0 || t.offsetHeight > 0) {
        trigger = t;
        break;
      }
    }
    if (trigger) positionPopover(popover, trigger);
  };

  popover.addEventListener("beforetoggle", (e) => {
    if (e.newState === "open") updatePos();
  });
  popover.addEventListener("toggle", (e) => {
    if (e.newState === "open") updatePos();
  });
});

/* @license-end */
