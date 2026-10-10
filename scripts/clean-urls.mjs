// Convert VitePress .html output to directory-style URLs so the site works
// on plain static hosts (GitHub Pages has no extensionless .html fallback).
// foo/bar.html -> foo/bar/index.html. Skips */index.html and 404.html.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dist = fileURLToPath(new URL("../.vitepress/dist/", import.meta.url));
let moved = 0;

for (const file of walk(dist)) {
  const rel = path.relative(dist, file);
  const base = path.basename(file);
  if (!base.endsWith(".html") || base === "index.html" || base === "404.html") continue;
  const dir = path.join(path.dirname(file), base.slice(0, -".html".length));
  const target = path.join(dir, "index.html");
  if (fs.existsSync(target)) {
    console.warn(`collision, skipped: ${rel}`);
    continue;
  }
  fs.mkdirSync(dir, { recursive: true });
  fs.renameSync(file, target);
  moved++;
}
console.log(`clean-urls: moved ${moved} pages`);

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}
