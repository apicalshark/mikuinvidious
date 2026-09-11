#!/usr/bin/env node
/**
 * Sync browser JS libraries from node_modules/ into static/.
 *
 * Single source of truth for vendored frontend libs: bump the version in
 * package.json, run `npm install` (postinstall re-runs this automatically),
 * and commit the refreshed files under static/.
 *
 * Usage:
 *   npm run sync:static          # update + copy, pointers stripped, no maps
 *   npm run sync:static:maps     # update + copy with source maps (debugging)
 *   node tools/sync-static.js --check         # exit 1 if any dest is out of date
 *   node tools/sync-static.js --with-maps     # copy <dest>.map files too
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
// Release default: strip sourceMappingURL pointers and skip .map files so
// ~9.5 MB of maps never lands in git or the Docker image. Pass
// --with-maps for local debugging (copies <dest>.map, rewrites pointer).
const includeMaps = process.argv.includes("--with-maps");
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
  // Source map (opt-in via --with-maps): copy as <dest>.map and point the
  // trailing sourceMappingURL comment at it (dest basename may differ).
  // Default release behavior: strip the pointer, no map file.
  const mapFrom = map && includeMaps ? path.join(NM, path.dirname(src), map) : null;
  const mapTo = map && includeMaps ? `${to}.map` : null;
  if (mapFrom && !fs.existsSync(mapFrom)) {
    console.error(`MISSING MAP: ${path.dirname(src)}/${map} (package ${pkg})`);
    failed++;
    return;
  }
  if (checkOnly) {
    // Compare JS ignoring the pointer line, plus the map bytes when opted in.
    // Without maps, a leftover <dest>.map or a present pointer counts as stale.
    const destJs = fs.existsSync(to) ? fs.readFileSync(to, "utf8") : null;
    let stale =
      destJs === null ||
      withoutMapPointer(fs.readFileSync(from, "utf8")) !== withoutMapPointer(destJs);
    if (mapTo) stale = stale || !fs.existsSync(mapTo) || sha1(mapFrom) !== sha1(mapTo);
    else if (map && (fs.existsSync(`${to}.map`) || /\/\/[#@]\s*sourceMappingURL=\S+/.test(destJs)))
      stale = true;
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
    } else {
      // Strip pointer; drop any map left over from a --with-maps run.
      const content = fs.readFileSync(to, "utf8");
      const updated = withoutMapPointer(content).replace(/\s*$/, "\n");
      if (updated !== content) fs.writeFileSync(to, updated);
      if (map && fs.existsSync(`${to}.map`)) fs.rmSync(`${to}.map`);
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
