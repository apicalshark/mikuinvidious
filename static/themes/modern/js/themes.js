/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

"use strict";
var toggle_theme = document.getElementById("toggle_theme");
toggle_theme.href = "javascript:void(0)";

var STORAGE_KEY_THEME = "dark_mode";
var THEME_DARK = "dark";
var THEME_LIGHT = "light";

// TODO: theme state controlled by system
toggle_theme.addEventListener("click", function () {
  const isDarkTheme = helpers.storage.get(STORAGE_KEY_THEME) === THEME_DARK;
  const newTheme = isDarkTheme ? THEME_LIGHT : THEME_DARK;
  setTheme(newTheme);
  helpers.storage.set(STORAGE_KEY_THEME, newTheme);
  helpers.xhr("GET", "/toggle_theme?redirect=false", {}, {});
});

var toggle_opencc = document.getElementById("toggle_opencc");
if (toggle_opencc) {
  toggle_opencc.addEventListener("click", function () {
    toggle_opencc.disabled = true;
    toggle_opencc.innerText = "...";
    helpers.xhr(
      "GET",
      "/toggle_opencc",
      {},
      {
        on200: function () {
          location.reload();
        },
        onNon200: function () {
          location.reload();
        },
        onError: function () {
          location.reload();
        },
      }
    );
  });
}

var toggle_search_opencc = document.getElementById("toggle_search_opencc");
if (toggle_search_opencc) {
  toggle_search_opencc.addEventListener("click", function () {
    toggle_search_opencc.disabled = true;
    toggle_search_opencc.innerText = "...";
    helpers.xhr(
      "GET",
      "/toggle_search_opencc",
      {},
      {
        on200: function () {
          location.reload();
        },
        onNon200: function () {
          location.reload();
        },
        onError: function () {
          location.reload();
        },
      }
    );
  });
}

/** @param {THEME_DARK|THEME_LIGHT} theme */
function setTheme(theme) {
  if (theme === THEME_DARK) {
    toggle_theme.children[0].className = "icon ion-ios-sunny";
    document.documentElement.classList.add("dark");
    document.documentElement.classList.remove("light");
  } else {
    toggle_theme.children[0].className = "icon ion-ios-moon";
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  }
}

// Handles theme change event caused by other tab
addEventListener("storage", function (e) {
  if (e.key === STORAGE_KEY_THEME) setTheme(helpers.storage.get(STORAGE_KEY_THEME));
});

// Set theme from preferences on page load
addEventListener("DOMContentLoaded", function () {
  const prefTheme = document.getElementById("dark_mode_pref").textContent;
  if (prefTheme) {
    setTheme(prefTheme);
    helpers.storage.set(STORAGE_KEY_THEME, prefTheme);
  }
});

/* @license-end */
