/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

"use strict";
// Contains only auxiliary methods
// May be included and executed unlimited number of times without any consequences

// Polyfills for IE11
Array.prototype.find =
  Array.prototype.find ||
  function (condition) {
    return this.filter(condition)[0];
  };
Array.from =
  Array.from ||
  function (source) {
    return Array.prototype.slice.call(source);
  };
NodeList.prototype.forEach =
  NodeList.prototype.forEach ||
  function (callback) {
    Array.from(this).forEach(callback);
  };
String.prototype.includes =
  String.prototype.includes ||
  function (searchString) {
    return this.indexOf(searchString) >= 0;
  };
String.prototype.startsWith =
  String.prototype.startsWith ||
  function (prefix) {
    return this.substr(0, prefix.length) === prefix;
  };
Math.sign =
  Math.sign ||
  function (x) {
    x = +x;
    if (!x) return x; // 0 and NaN
    return x > 0 ? 1 : -1;
  };
if (
  !window.hasOwnProperty("HTMLDetailsElement") &&
  !window.hasOwnProperty("mockHTMLDetailsElement")
) {
  window.mockHTMLDetailsElement = true;
  const style = "details:not([open]) > :not(summary) {display: none}";
  document.head.appendChild(document.createElement("style")).textContent = style;

  addEventListener("click", function (e) {
    if (e.target.nodeName !== "SUMMARY") return;
    const details = e.target.parentElement;
    if (details.hasAttribute("open")) details.removeAttribute("open");
    else details.setAttribute("open", "");
  });
}

// Monstrous global variable for handy code
// Includes: clamp, xhr, storage.{get,set,remove}
window.helpers = window.helpers || {
  /**
   * https://en.wikipedia.org/wiki/Clamping_(graphics)
   * @param {Number} num Source number
   * @param {Number} min Low border
   * @param {Number} max High border
   * @returns {Number} Clamped value
   */
  clamp: function (num, min, max) {
    if (max < min) {
      var t = max;
      max = min;
      min = t; // swap max and min
    }

    if (max < num) return max;
    if (min > num) return min;
    return num;
  },

  /** @private */
  _xhr: async function (method, url, options, callbacks) {
    const fetchOptions = {
      method: method,
      headers: {},
    };

    if (method === "POST") {
      fetchOptions.headers["Content-Type"] = "application/x-www-form-urlencoded";
    }

    if (options.payload) {
      fetchOptions.body = options.payload;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), options.timeout || 10000);
    fetchOptions.signal = controller.signal;

    try {
      const response = await fetch(url, fetchOptions);
      clearTimeout(timeoutId);

      if (response.ok) {
        if (callbacks.on200) {
          const data = await (options.responseType === "text" ? response.text() : response.json());
          callbacks.on200(data);
        }
      } else {
        if (callbacks.onNon200) {
          callbacks.onNon200({ status: response.status, statusText: response.statusText });
        }
      }
    } catch (error) {
      clearTimeout(timeoutId);
      if (error.name === "AbortError") {
        if (callbacks.onTimeout) callbacks.onTimeout(error);
      } else {
        if (callbacks.onError) callbacks.onError(error);
      }
    }
  },
  /** @private */
  _xhrRetry: function (method, url, options, callbacks) {
    if (options.retries <= 0) {
      console.warn("Failed to pull", options.entity_name);
      if (callbacks.onTotalFail) callbacks.onTotalFail();
      return;
    }
    helpers._xhr(method, url, options, callbacks);
  },
  /**
   * @callback callbackXhrOn200
   * @param {Object} response - xhr.response
   */
  /**
   * @callback callbackXhrError
   * @param {XMLHttpRequest} xhr
   */
  /**
   * @param {'GET'|'POST'} method - 'GET' or 'POST'
   * @param {String} url - URL to send request to
   * @param {Object} options - other XHR options
   * @param {XMLHttpRequestBodyInit} [options.payload=null] - payload for POST-requests
   * @param {'arraybuffer'|'blob'|'document'|'json'|'text'} [options.responseType=json]
   * @param {Number} [options.timeout=10000]
   * @param {Number} [options.retries=1]
   * @param {String} [options.entity_name='unknown'] - string to log
   * @param {Number} [options.retry_timeout=1000]
   * @param {Object} callbacks - functions to execute on events fired
   * @param {callbackXhrOn200} [callbacks.on200]
   * @param {callbackXhrError} [callbacks.onNon200]
   * @param {callbackXhrError} [callbacks.onTimeout]
   * @param {callbackXhrError} [callbacks.onError]
   * @param {callbackXhrError} [callbacks.onTotalFail] - if failed after all retries
   */
  xhr: function (method, url, options, callbacks) {
    if (!options.retries || options.retries <= 1) {
      helpers._xhr(method, url, options, callbacks);
      return;
    }

    if (!options.entity_name) options.entity_name = "unknown";
    if (!options.retry_timeout) options.retry_timeout = 1000;
    const retries_total = options.retries;
    let currentTry = 1;

    const retry = function () {
      console.warn(
        "Pulling " + options.entity_name + " failed... " + currentTry++ + "/" + retries_total
      );
      setTimeout(function () {
        options.retries--;
        helpers._xhrRetry(method, url, options, callbacks);
      }, options.retry_timeout);
    };

    // Pack retry() call into error handlers
    callbacks._onError = callbacks.onError;
    callbacks.onError = function (error) {
      if (callbacks._onError) callbacks._onError(error);
      retry();
    };
    callbacks._onTimeout = callbacks.onTimeout;
    callbacks.onTimeout = function (error) {
      if (callbacks._onTimeout) callbacks._onTimeout(error);
      retry();
    };

    helpers._xhrRetry(method, url, options, callbacks);
  },

  /**
   * @typedef {Object} invidiousStorage
   * @property {(key:String) => Object} get
   * @property {(key:String, value:Object)} set
   * @property {(key:String)} remove
   */

  /**
   * Universal storage, stores and returns JS objects. Uses inside localStorage or cookies
   * @type {invidiousStorage}
   */
  storage: (function () {
    // access to localStorage throws exception in Tor Browser, so try is needed
    let localStorageIsUsable = false;
    try {
      localStorageIsUsable = !!localStorage.setItem;
    } catch (e) {}

    if (localStorageIsUsable) {
      return {
        get: function (key) {
          if (!localStorage[key]) return;
          try {
            return JSON.parse(decodeURIComponent(localStorage[key]));
          } catch (e) {
            // Erase non parsable value
            helpers.storage.remove(key);
          }
        },
        set: function (key, value) {
          localStorage[key] = encodeURIComponent(JSON.stringify(value));
        },
        remove: function (key) {
          localStorage.removeItem(key);
        },
      };
    }

    // TODO: fire 'storage' event for cookies
    console.info("Storage: localStorage is disabled or unaccessible. Cookies used as fallback");
    return {
      get: function (key) {
        const cookiePrefix = key + "=";
        function findCallback(cookie) {
          return cookie.startsWith(cookiePrefix);
        }
        const matchedCookie = document.cookie.split("; ").find(findCallback);
        if (matchedCookie) {
          const cookieBody = matchedCookie.replace(cookiePrefix, "");
          if (cookieBody.length === 0) return;
          try {
            return JSON.parse(decodeURIComponent(cookieBody));
          } catch (e) {
            // Erase non parsable value
            helpers.storage.remove(key);
          }
        }
      },
      set: function (key, value) {
        const cookie_data = encodeURIComponent(JSON.stringify(value));

        // Set expiration in 30 days
        const date = new Date();
        date.setDate(date.getDate() + 30);
        const secureFlag = location.protocol === "https:" ? "; Secure" : "";

        document.cookie =
          key +
          "=" +
          cookie_data +
          "; expires=" +
          date.toGMTString() +
          "; SameSite=Lax" +
          secureFlag;
      },
      remove: function (key) {
        document.cookie = key + "=; Max-Age=0; SameSite=Lax";
      },
    };
  })(),
};

// Minimal gettext-style lookup against the server-injected catalog
// (templates/themes/modern/base.html: window.I18N = {locale, msgs} from PO
// via tools/po2json.py; plain {msgid: msgstr} strings, plural entries as
// form arrays under the singular msgid, plus a __plural metadata entry).
// Reads window.I18N lazily at call time, so script order (this file loads
// BEFORE the injection) does not matter. Empty/missing msgstr falls back
// to the English msgid. No external deps. NOTE: window.I18n (helper) and
// window.I18N (catalog) are intentionally different globals — do not
// merge them.
window.I18n = window.I18n || {
  _catalog: function () {
    return (window.I18N && window.I18N.msgs) || {};
  },
  _fill: function (s, vars) {
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        var v = String(vars[k]);
        s = s
          .split("%(" + k + ")s")
          .join(v)
          .split("{" + k + "}")
          .join(v);
      });
    }
    return s;
  },
  /**
   * @param {String} msgid - English source string (must match PO msgid)
   * @param {Object} [vars] - {name: value} for %(name)s / {name} placeholders
   * @returns {String} translated + substituted string, or msgid fallback
   */
  t: function (msgid, vars) {
    var catalog = this._catalog();
    var s = Object.prototype.hasOwnProperty.call(catalog, msgid) ? catalog[msgid] : msgid;
    if (Array.isArray(s)) s = s[0];
    if (typeof s !== "string" || s === "") s = msgid;
    return this._fill(s, vars);
  },
  /**
   * Plural-aware lookup (replaces inline count===1 ternaries).
   * @param {String} singular - singular English msgid (plural forms live under it)
   * @param {String} plural - plural English msgid (legacy-catalog fallback only)
   * @param {Number} n - count selecting the form
   * @param {Object} [vars] - placeholder substitutions
   * @returns {String} translated + substituted string
   */
  n: function (singular, plural, n, vars) {
    var catalog = this._catalog();
    var num = Number(n);
    var val = Object.prototype.hasOwnProperty.call(catalog, singular) ? catalog[singular] : null;
    var s = null;
    if (Array.isArray(val) && val.length) {
      var order = (catalog.__plural && catalog.__plural.order) || ["one", "other"];
      var tag = num === 1 ? "one" : "other";
      try {
        tag = new Intl.PluralRules((window.I18N && window.I18N.locale) || "en").select(num);
      } catch (e) {
        /* keep English-style fallback tag */
      }
      var idx = order.indexOf(tag);
      if (idx < 0) idx = num === 1 ? 0 : val.length - 1;
      idx = Math.max(0, Math.min(idx, val.length - 1));
      s = val[idx] || val[0];
    } else {
      s = num === 1 ? (typeof val === "string" && val ? val : singular) : this.t(plural);
    }
    if (typeof s !== "string" || s === "") s = num === 1 ? singular : plural;
    return this._fill(s, vars);
  },
};

/* @license-end */
