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

function handleThemeToggle() {
  const isDarkTheme = helpers.storage.get(STORAGE_KEY_THEME) === THEME_DARK;
  const newTheme = isDarkTheme ? THEME_LIGHT : THEME_DARK;
  setTheme(newTheme);
  helpers.storage.set(STORAGE_KEY_THEME, newTheme);
  
  // Get CSRF token from meta tag or form
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || 
                    document.querySelector('input[name="csrf_token"]')?.value;
  if (csrfToken) {
    helpers.xhr("POST", "/toggle_theme", { payload: `csrf_token=${encodeURIComponent(csrfToken)}` }, {});
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
      "search_opencc=" + newVal + "; path=/; max-age=" + 3600 * 24 * 30 + "; SameSite=Lax" + secureFlag;
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

  const openccPref = helpers.storage.get("opencc") || getCookie("opencc");

  if (openccPref === "1") {
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
      btnOpencc.innerText = "已开启";
    } else {
      btnOpencc.classList.remove("is-on");
      btnOpencc.innerText = "已关闭";
    }
  }

  const searchOpenccPref = helpers.storage.get("search_opencc") || getCookie("search_opencc");
  const btnSearchOpencc = document.getElementById("toggle_search_opencc");
  if (btnSearchOpencc) {
    if (searchOpenccPref === "1") {
      btnSearchOpencc.classList.add("is-on");
      btnSearchOpencc.innerText = "已开启";
    } else {
      btnSearchOpencc.classList.remove("is-on");
      btnSearchOpencc.innerText = "已关闭";
    }
  }

  if (searchOpenccPref === "1") {
    const searchForm = document.querySelector('form[action="/search"]');
    if (searchForm) {
      const searchBox = document.getElementById("searchbox");
      const s2sConverter = OpenCC.Converter({ from: "tw", to: "cn" });
      searchForm.addEventListener("submit", function () {
        searchBox.value = s2sConverter(searchBox.value);
      });
    }
  }
}

if (document.readyState === "loading") {
  addEventListener("DOMContentLoaded", initPreferences);
} else {
  initPreferences();
}

/* @license-end */
