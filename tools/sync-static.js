#!/usr/bin/env node
/**
 * Sync browser JS libraries from node_modules/ into static/.
 *
 * Single source of truth for vendored frontend libs: bump the version in
 * package.json, run `npm install` (postinstall re-runs this automatically),
 * and commit the refreshed files under static/.
 *
 * Usage:
 *   npm run sync:static          # copy node_modules dist files -> static/
 *   node tools/sync-static.js --check   # exit 1 if any dest is out of date
 */
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..");
const NM = path.join(ROOT, "node_modules");

// { pkg, src (inside node_modules), dest (inside static/), map? (source map
// basename next to src; copied as <dest>.map with the pointer rewritten) }
const MANIFEST = [
  { pkg: "hls.js", src: "hls.js/dist/hls.min.js", dest: "vjs/hls.min.js", map: "hls.min.js.map" },
  { pkg: "mpegts.js", src: "mpegts.js/dist/mpegts.js", dest: "vjs/mpegts.js", map: "mpegts.js.map" },
  { pkg: "media-chrome", src: "media-chrome/dist/iife/index.js", dest: "vjs/media-chrome.js", map: "index.js.map" },
  { pkg: "hls-video-element", src: "hls-video-element/dist/hls-video-element.js", dest: "vjs/hls-video-element.js" },
  { pkg: "opencc-js", src: "opencc-js/dist/umd/full.js", dest: "opencc-js/opencc.js" },
  // dashjs v5 ships legacy (UMD global `dashjs`, used by player.js) and
  // modern (ESM) builds; plain <script> tags need the legacy UMD bundle.
  { pkg: "dashjs", src: "dashjs/dist/legacy/umd/dash.all.min.js", dest: "vjs/dash.min.js", map: "dash.all.min.js.map" },
  { pkg: "dashjs", src: "dashjs/dist/legacy/umd/dash.all.min.js.LICENSE.txt", dest: "vjs/dash.all.min.js.LICENSE.txt" },
  { pkg: "danmaku", src: "danmaku/dist/danmaku.min.js", dest: "danmakujs/danmaku.min.js" },
  { pkg: "danmaku", src: "danmaku/dist/danmaku.dom.min.js", dest: "danmakujs/danmaku.dom.min.js" },
  { pkg: "danmaku", src: "danmaku/dist/danmaku.canvas.min.js", dest: "danmakujs/danmaku.canvas.min.js" },
];

function pkgVersion(pkg) {
  const meta = JSON.parse(fs.readFileSync(path.join(NM, pkg, "package.json"), "utf8"));
  return meta.version;
}

function sha1(file) {
  return crypto.createHash("sha1").update(fs.readFileSync(file)).digest("hex").slice(0, 8);
}

const checkOnly = process.argv.includes("--check");
let failed = 0;

function withoutMapPointer(content) {
  return content.replace(/\/\/[#@]\s*sourceMappingURL=\S+\s*$/, "");
}

function syncEntry({ pkg, src, dest, map }) {
  const from = path.join(NM, src);
  const to = path.join(ROOT, "static", dest);
  if (!fs.existsSync(from)) {
    console.error(`MISSING: ${src} (package ${pkg} not installed? run npm install)`);
    failed++;
    return;
  }
  const label = `${dest}  (${pkg}@${pkgVersion(pkg)})`;
  // Source map: copy as <dest>.map and point the trailing
  // sourceMappingURL comment at it (dest basename may differ from src).
  const mapFrom = map ? path.join(NM, path.dirname(src), map) : null;
  const mapTo = map ? `${to}.map` : null;
  if (mapFrom && !fs.existsSync(mapFrom)) {
    console.error(`MISSING MAP: ${path.dirname(src)}/${map} (package ${pkg})`);
    failed++;
    return;
  }
  if (checkOnly) {
    // Compare JS ignoring the (rewritten) pointer line, plus the map bytes.
    let stale =
      !fs.existsSync(to) ||
      withoutMapPointer(fs.readFileSync(from, "utf8")) !== withoutMapPointer(fs.readFileSync(to, "utf8"));
    if (mapTo) stale = stale || !fs.existsSync(mapTo) || sha1(mapFrom) !== sha1(mapTo);
    console.log(`${stale ? "STALE " : "OK     "} ${label}`);
    if (stale) failed++;
  } else {
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    if (mapTo) {
      fs.copyFileSync(mapFrom, mapTo);
      const content = fs.readFileSync(to, "utf8");
      const updated = content.replace(
        /\/\/[#@]\s*sourceMappingURL=\S+\s*$/,
        `//# sourceMappingURL=${path.basename(mapTo)}`,
      );
      if (updated !== content) fs.writeFileSync(to, updated);
    }
    console.log(`synced ${label}${mapTo ? " +map" : ""}`);
  }
}

for (const entry of MANIFEST) syncEntry(entry);

if (failed > 0) {
  console.error(checkOnly ? `${failed} file(s) out of date — run: npm run sync:static` : `${failed} file(s) failed`);
  process.exit(1);
}
console.log(checkOnly ? "All vendored libs up to date." : "Done.");
