/* Material You (Material Design 3) OKLCH Theme Engine for MikuInvidious
   AGPL-3.0 License */
"use strict";

(function (window) {
  const PRESETS = {
    sakura: "#a85383",
    bili_pink: "#fb7299",
    bili_blue: "#00aeec",
    miku: "#39c5bb",
    orange: "#ff7a00",
    lavender: "#795290",
    ocean: "#2563eb",
  };

  const STORAGE_KEY_SEED = "m3_seed";
  const DEFAULT_SEED =
    (typeof document !== "undefined" &&
      document.querySelector('meta[name="default-accent"]')?.getAttribute("content")) ||
    PRESETS.sakura;

  function isSakura(seed) {
    if (!seed || seed === "default") {
      const def = (DEFAULT_SEED || "").toLowerCase().trim();
      return def === PRESETS.sakura || def === "sakura" || def === "#ff97b5";
    }
    const s = seed.toLowerCase().trim();
    return s === PRESETS.sakura || s === "sakura" || s === "#ff97b5";
  }

  function isDefault(seed) {
    if (!seed || seed === "default") return true;
    const s = seed.toLowerCase().trim();
    const def = (DEFAULT_SEED || "").toLowerCase().trim();
    return s === def;
  }

  // Convert Hex to sRGB [0..1]
  function hexToRgb(hex) {
    let clean = hex.replace(/^#/, "");
    if (clean.length === 3) {
      clean = clean.split("").map((c) => c + c).join("");
    }
    const num = parseInt(clean, 16);
    return [
      ((num >> 16) & 255) / 255,
      ((num >> 8) & 255) / 255,
      (num & 255) / 255,
    ];
  }

  // sRGB to linear sRGB
  function srgbToLinear(c) {
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }

  // Linear sRGB to OKLab
  function rgbToOklab(r, g, b) {
    const lr = srgbToLinear(r);
    const lg = srgbToLinear(g);
    const lb = srgbToLinear(b);

    const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
    const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
    const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);

    return [
      0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
      1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
      0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
    ];
  }

  // OKLab to OKLCH
  function oklabToOklch(L, a, b) {
    const C = Math.sqrt(a * a + b * b);
    let H = (Math.atan2(b, a) * 180) / Math.PI;
    if (H < 0) H += 360;
    return [L, C, H];
  }

  function hexToOklch(hex) {
    try {
      const [r, g, b] = hexToRgb(hex);
      const [L, a, bOk] = rgbToOklab(r, g, b);
      return oklabToOklch(L, a, bOk);
    } catch {
      return [0.55, 0.15, 350]; // Fallback to Sakura
    }
  }

  const ALL_TOKEN_PROPS = [
    "--theme-primary",
    "--md-sys-color-primary",
    "--md-sys-color-on-primary",
    "--md-sys-color-primary-container",
    "--md-sys-color-on-primary-container",
    "--md-sys-color-secondary",
    "--md-sys-color-on-secondary",
    "--md-sys-color-secondary-container",
    "--md-sys-color-on-secondary-container",
    "--md-sys-color-tertiary",
    "--md-sys-color-tertiary-container",
    "--md-sys-color-on-tertiary-container",
    "--md-sys-color-background",
    "--md-sys-color-on-background",
    "--md-sys-color-surface",
    "--md-sys-color-on-surface",
    "--md-sys-color-surface-variant",
    "--md-sys-color-on-surface-variant",
    "--md-sys-color-surface-container-lowest",
    "--md-sys-color-surface-container-low",
    "--md-sys-color-surface-container",
    "--md-sys-color-surface-container-high",
    "--md-sys-color-surface-container-highest",
    "--md-sys-color-outline",
    "--md-sys-color-outline-variant",
    "--player-primary",
    "--player-on-primary",
    "--media-primary-color",
  ];

  function computeTokens(hex, isDark) {
    // Exact original Sakura theme tokens when sakura seed is active
    if (isSakura(hex)) {
      if (isDark) {
        return {
          "--theme-primary": "#ff97b5",
          "--md-sys-color-primary": "#ff97b5",
          "--md-sys-color-on-primary": "color-mix(in srgb, #ff97b5, black 80%)",
          "--md-sys-color-primary-container": "color-mix(in srgb, #ff97b5, black 55%)",
          "--md-sys-color-on-primary-container": "color-mix(in srgb, #ff97b5, white 65%)",
          "--md-sys-color-secondary": "color-mix(in srgb, #ff97b5, white 40%)",
          "--md-sys-color-on-secondary": "color-mix(in srgb, #ff97b5, black 80%)",
          "--md-sys-color-secondary-container": "color-mix(in srgb, #ff97b5, black 55%)",
          "--md-sys-color-on-secondary-container": "color-mix(in srgb, #ff97b5, white 80%)",
          "--md-sys-color-tertiary": "color-mix(in srgb, #ff97b5, #60a5fa 40%)",
          "--md-sys-color-tertiary-container": "color-mix(in srgb, #ff97b5, black 50%)",
          "--md-sys-color-on-tertiary-container": "color-mix(in srgb, #ff97b5, white 85%)",
          "--md-sys-color-background": "black",
          "--md-sys-color-on-background": "#ECE0E3",
          "--md-sys-color-surface": "oklch(0.11 0.012 350)",
          "--md-sys-color-on-surface": "#ECE0E3",
          "--md-sys-color-surface-container-lowest": "oklch(0.06 0.008 350)",
          "--md-sys-color-surface-container-low": "oklch(0.13 0.014 350)",
          "--md-sys-color-surface-container": "oklch(0.17 0.018 350)",
          "--md-sys-color-surface-container-high": "oklch(0.22 0.022 350)",
          "--md-sys-color-surface-container-highest": "oklch(0.27 0.026 350)",
          "--md-sys-color-surface-variant": "oklch(0.22 0.025 350)",
          "--md-sys-color-on-surface-variant": "color-mix(in srgb, #ff97b5, white 80%)",
          "--md-sys-color-outline": "oklch(0.55 0.030 350)",
          "--md-sys-color-outline-variant": "oklch(0.32 0.020 350)",
          "--player-primary": "#ff97b5",
          "--player-on-primary": "#000000",
          "--media-primary-color": "#ff97b5",
        };
      } else {
        return {
          "--theme-primary": "#a85383",
          "--md-sys-color-primary": "#a85383",
          "--md-sys-color-on-primary": "white",
          "--md-sys-color-primary-container": "color-mix(in srgb, #a85383, white 65%)",
          "--md-sys-color-on-primary-container": "color-mix(in srgb, #a85383, black 80%)",
          "--md-sys-color-secondary": "color-mix(in srgb, #a85383, #605D62 30%)",
          "--md-sys-color-on-secondary": "white",
          "--md-sys-color-secondary-container": "color-mix(in srgb, #a85383, white 75%)",
          "--md-sys-color-on-secondary-container": "color-mix(in srgb, #a85383, black 80%)",
          "--md-sys-color-tertiary": "color-mix(in srgb, #a85383, #2563eb 40%)",
          "--md-sys-color-tertiary-container": "color-mix(in srgb, #a85383, white 80%)",
          "--md-sys-color-on-tertiary-container": "color-mix(in srgb, #a85383, black 75%)",
          "--md-sys-color-background": "#ffffff",
          "--md-sys-color-on-background": "#201A1D",
          "--md-sys-color-surface": "#fdfcfc",
          "--md-sys-color-on-surface": "#201A1D",
          "--md-sys-color-surface-container-lowest": "#ffffff",
          "--md-sys-color-surface-container-low": "oklch(0.992 0.004 350)",
          "--md-sys-color-surface-container": "oklch(0.978 0.006 350)",
          "--md-sys-color-surface-container-high": "oklch(0.960 0.009 350)",
          "--md-sys-color-surface-container-highest": "oklch(0.940 0.012 350)",
          "--md-sys-color-surface-variant": "oklch(0.965 0.008 350)",
          "--md-sys-color-on-surface-variant": "color-mix(in srgb, #a85383, black 60%)",
          "--md-sys-color-outline": "oklch(0.52 0.030 350)",
          "--md-sys-color-outline-variant": "oklch(0.900 0.008 350)",
          "--player-primary": "#ff97b5",
          "--player-on-primary": "#000000",
          "--media-primary-color": "#ff97b5",
        };
      }
    }

    const [L, C, H] = hexToOklch(hex);
    const h = Math.round(H);
    const c = Math.max(0.04, Math.min(C, 0.28));
    const hTertiary = (h + 60) % 360;

    // Player chrome is always on a black surface (#000000).
    // If the accent is bright (L >= 0.60, like #fb7299, #00aeec, #39c5bb, #ff7a00),
    // use the exact brand seed color. If darker (like lavender or deep blue),
    // lift luminance to OKLCH L=0.78 for pristine contrast on black.
    const playerPrimary = L < 0.60 ? `oklch(0.78 ${c.toFixed(3)} ${h})` : hex;
    const playerOnPrimary = "#000000";

    if (isDark) {
      return {
        "--theme-primary": hex,
        "--md-sys-color-primary": `oklch(0.80 ${c.toFixed(3)} ${h})`,
        "--md-sys-color-on-primary": `oklch(0.20 ${(c * 0.8).toFixed(3)} ${h})`,
        "--md-sys-color-primary-container": `oklch(0.35 ${c.toFixed(3)} ${h})`,
        "--md-sys-color-on-primary-container": `oklch(0.92 ${(c * 0.5).toFixed(3)} ${h})`,
        "--md-sys-color-secondary": `oklch(0.78 ${(c * 0.5).toFixed(3)} ${h})`,
        "--md-sys-color-on-secondary": `oklch(0.20 ${(c * 0.4).toFixed(3)} ${h})`,
        "--md-sys-color-secondary-container": `oklch(0.30 ${(c * 0.5).toFixed(3)} ${h})`,
        "--md-sys-color-on-secondary-container": `oklch(0.90 ${(c * 0.4).toFixed(3)} ${h})`,
        "--md-sys-color-tertiary": `oklch(0.80 ${(c * 0.7).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-tertiary-container": `oklch(0.32 ${(c * 0.7).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-on-tertiary-container": `oklch(0.92 ${(c * 0.4).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-background": `oklch(0.09 0.010 ${h})`,
        "--md-sys-color-on-background": `oklch(0.90 0.015 ${h})`,
        "--md-sys-color-surface": `oklch(0.11 0.012 ${h})`,
        "--md-sys-color-on-surface": `oklch(0.90 0.015 ${h})`,
        "--md-sys-color-surface-variant": `oklch(0.22 0.025 ${h})`,
        "--md-sys-color-on-surface-variant": `oklch(0.78 0.025 ${h})`,
        "--md-sys-color-surface-container-lowest": `oklch(0.06 0.008 ${h})`,
        "--md-sys-color-surface-container-low": `oklch(0.13 0.014 ${h})`,
        "--md-sys-color-surface-container": `oklch(0.17 0.018 ${h})`,
        "--md-sys-color-surface-container-high": `oklch(0.22 0.022 ${h})`,
        "--md-sys-color-surface-container-highest": `oklch(0.27 0.026 ${h})`,
        "--md-sys-color-outline": `oklch(0.55 0.030 ${h})`,
        "--md-sys-color-outline-variant": `oklch(0.32 0.020 ${h})`,
        "--player-primary": playerPrimary,
        "--player-on-primary": playerOnPrimary,
        "--media-primary-color": playerPrimary,
      };
    } else {
      const targetL = Math.min(L, 0.61);
      const balancedPrimary = `oklch(${targetL.toFixed(2)} ${c.toFixed(3)} ${h})`;
      const cLow = Math.max(0.003, Math.min(0.005, c * 0.025));
      const cMid = Math.max(0.005, Math.min(0.008, c * 0.04));
      const cHigh = Math.max(0.007, Math.min(0.012, c * 0.06));
      const cHighest = Math.max(0.010, Math.min(0.016, c * 0.08));
      return {
        "--theme-primary": hex,
        "--md-sys-color-primary": balancedPrimary,
        "--md-sys-color-on-primary": "#ffffff",
        "--md-sys-color-primary-container": `oklch(0.92 ${(c * 0.4).toFixed(3)} ${h})`,
        "--md-sys-color-on-primary-container": `oklch(0.20 ${(c * 0.6).toFixed(3)} ${h})`,
        "--md-sys-color-secondary": `oklch(0.55 ${(c * 0.4).toFixed(3)} ${h})`,
        "--md-sys-color-on-secondary": "#ffffff",
        "--md-sys-color-secondary-container": `oklch(0.94 ${(c * 0.3).toFixed(3)} ${h})`,
        "--md-sys-color-on-secondary-container": `oklch(0.22 ${(c * 0.4).toFixed(3)} ${h})`,
        "--md-sys-color-tertiary": `oklch(0.55 ${(c * 0.6).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-tertiary-container": `oklch(0.94 ${(c * 0.4).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-on-tertiary-container": `oklch(0.20 ${(c * 0.6).toFixed(3)} ${hTertiary})`,
        "--md-sys-color-background": "#ffffff",
        "--md-sys-color-on-background": `oklch(0.18 0.015 ${h})`,
        "--md-sys-color-surface": `oklch(0.995 0.002 ${h})`,
        "--md-sys-color-on-surface": `oklch(0.18 0.015 ${h})`,
        "--md-sys-color-surface-variant": `oklch(0.965 ${cHigh.toFixed(3)} ${h})`,
        "--md-sys-color-on-surface-variant": `oklch(0.38 0.025 ${h})`,
        "--md-sys-color-surface-container-lowest": "#ffffff",
        "--md-sys-color-surface-container-low": `oklch(0.992 ${cLow.toFixed(3)} ${h})`,
        "--md-sys-color-surface-container": `oklch(0.978 ${cMid.toFixed(3)} ${h})`,
        "--md-sys-color-surface-container-high": `oklch(0.960 ${cHigh.toFixed(3)} ${h})`,
        "--md-sys-color-surface-container-highest": `oklch(0.940 ${cHighest.toFixed(3)} ${h})`,
        "--md-sys-color-outline": `oklch(0.52 0.030 ${h})`,
        "--md-sys-color-outline-variant": `oklch(0.900 ${(c * 0.06).toFixed(3)} ${h})`,
        "--player-primary": playerPrimary,
        "--player-on-primary": playerOnPrimary,
        "--media-primary-color": playerPrimary,
      };
    }
  }

  function getStoredSeed() {
    try {
      const match = document.cookie.match(/(?:^|;\s*)m3_seed=([^;]+)/);
      if (match) return decodeURIComponent(match[1]);
      const stored = localStorage.getItem(STORAGE_KEY_SEED);
      if (stored) return stored;
    } catch {}
    return DEFAULT_SEED;
  }

  function applyTokens(hex, isDark) {
    if (typeof isDark !== "boolean") {
      isDark = document.documentElement.classList.contains("dark");
    }
    const seed = hex && typeof hex === "string" ? hex : getStoredSeed();
    const root = document.documentElement;

    const tokens = computeTokens(seed, isDark);
    for (const [prop, val] of Object.entries(tokens)) {
      root.style.setProperty(prop, val);
    }
  }

  function setSeedColor(hex) {
    try {
      if (isDefault(hex)) {
        localStorage.removeItem(STORAGE_KEY_SEED);
        document.cookie = "m3_seed=; path=/; max-age=0; SameSite=Lax";
        if (document.documentElement.dataset) {
          delete document.documentElement.dataset.m3Seed;
        }
      } else {
        localStorage.setItem(STORAGE_KEY_SEED, hex);
        const secure = location.protocol === "https:" ? "; Secure" : "";
        document.cookie = `m3_seed=${encodeURIComponent(hex)}; path=/; max-age=31536000; SameSite=Lax${secure}`;
        if (document.documentElement.dataset) {
          document.documentElement.dataset.m3Seed = hex;
        }
      }
    } catch {}
    applyTokens(hex);
  }

  function init() {
    const isDark = document.documentElement.classList.contains("dark");
    applyTokens(getStoredSeed(), isDark);
  }

  if (typeof document !== "undefined") {
    init();
  }

  window.M3Theme = {
    PRESETS,
    DEFAULT_SEED,
    isDefault,
    isSakura,
    computeTokens,
    hexToOklch,
    getStoredSeed,
    applyTokens,
    setSeedColor,
  };
})(typeof window !== "undefined" ? window : globalThis);
