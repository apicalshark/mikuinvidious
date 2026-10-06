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

  // Get CSRF token from meta tag or form
  const csrfToken =
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
    document.querySelector('input[name="csrf_token"]')?.value;
  if (csrfToken) {
    helpers.xhr(
      "POST",
      "/toggle_theme",
      { payload: `csrf_token=${encodeURIComponent(csrfToken)}` },
      {}
    );
  }
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
    s.src = "/static/opencc-js/opencc.js";
    s.defer = true;
    s.onload = function () { resolve(window.OpenCC); };
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return _openccPromise;
}

// Set preferences on page load
function initPreferences() {
  const dark_mode_pref_el = document.getElementById("dark_mode_pref");
  if (dark_mode_pref_el) {
    const prefTheme = dark_mode_pref_el.textContent;
    if (prefTheme) {
      setTheme(prefTheme);
      helpers.storage.set(STORAGE_KEY_THEME, prefTheme);
    }
  }

  const openccPref = (typeof helpers !== "undefined" && helpers.storage.get("opencc")) || getCookie("opencc");

  if (openccPref === "1") {
    ensureOpenCC().then(function () {
      if (!window.OpenCC) return;
      initOpenccConvert();
    }).catch(function () {});
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

  if (searchOpenccPref === "1") {
    const searchForm = document.querySelector('form[action="/search"]');
    if (searchForm) {
      // Kick off the lazy load immediately, but register the handler NOW so
      // a submit during the load still converts instead of bypassing.
      var openccReady = ensureOpenCC().catch(function () { return null; });
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
            searchForm.requestSubmit();
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

// Navbar language switcher: flag button toggles a quality-menu-styled
// dropdown; picking a language POSTs to /set_lang (same CSRF pattern as
// the theme toggle) and reloads. Works for both desktop and mobile menus.
function toggleLangMenu(menu, show) {
  if (!menu) return;
  if (show) {
    menu.classList.remove("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.add("opacity-100", "scale-100", "pointer-events-auto");
  } else {
    menu.classList.add("opacity-0", "pointer-events-none", "scale-95");
    menu.classList.remove("opacity-100", "scale-100", "pointer-events-auto");
  }
}

function closeAllLangMenus(except) {
  document.querySelectorAll("#lang_menu, #mobile_lang_menu").forEach((m) => {
    if (m !== except) toggleLangMenu(m, false);
  });
}

async function setLanguage(lang) {
  // Set cookie on client directly so it applies immediately
  const secureFlag = location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    "lang=" + encodeURIComponent(lang) + "; path=/; max-age=" + 3600 * 24 * 30 + "; SameSite=Lax" + secureFlag;

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

for (const [btnId, menuId] of [["lang_btn", "lang_menu"], ["mobile_lang_btn", "mobile_lang_menu"]]) {
  const btn = document.getElementById(btnId);
  const menu = document.getElementById(menuId);
  if (btn && menu) {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willShow = menu.classList.contains("opacity-0");
      closeAllLangMenus(menu);
      toggleLangMenu(menu, willShow);
    });
  }
}

document.querySelectorAll(".lang-option").forEach((opt) => {
  opt.addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllLangMenus(null);
    setLanguage(opt.dataset.lang);
  });
});

document.addEventListener("click", (e) => {
  if (!e.target.closest("#lang_menu, #mobile_lang_menu, #lang_btn, #mobile_lang_btn")) {
    closeAllLangMenus(null);
  }
});

/* @license-end */
