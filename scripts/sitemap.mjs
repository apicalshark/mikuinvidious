// Generate sitemap.xml from the built (directory-style) output.
// lastmod comes from git history of the source markdown file.
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = "https://apicalshark.github.io/mikuinvidious";
const root = path.dirname(fileURLToPath(new URL("../package.json", import.meta.url)));
const dist = path.join(root, ".vitepress", "dist");

const urls = [];
for (const file of walk(dist)) {
  const rel = path.relative(dist, file).replaceAll(path.sep, "/");
  if ((!rel.endsWith("/index.html") && rel !== "index.html") || rel === "404.html") continue;
  const route = rel === "index.html" ? "/" : "/" + rel.slice(0, -"/index.html".length);
  const src = sourceFor(rel);
  if (!src) {
    console.warn(`sitemap: no source for ${rel}, skipped`);
    continue;
  }
  urls.push({ loc: SITE + (route === "/" ? "/" : route + "/"), lastmod: lastmod(src) });
}

urls.sort((a, b) => (a.loc < b.loc ? -1 : 1));
const xml =
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
  `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
  urls.map((u) => `  <url><loc>${u.loc}</loc>${u.lastmod ? `<lastmod>${u.lastmod}</lastmod>` : ""}</url>`).join("\n") +
  `\n</urlset>\n`;
fs.writeFileSync(path.join(dist, "sitemap.xml"), xml);
console.log(`sitemap: ${urls.length} urls`);

function sourceFor(rel) {
  // dist path -> source markdown. Directory indexes (operators/index.html)
  // and converted pages (operators/configuration/index.html) look alike,
  // so resolve by whichever source file actually exists.
  const p = rel.slice(0, -"/index.html".length);
  for (const cand of [p === "" ? "index.md" : `${p}.md`, p === "" ? null : `${p}/index.md`]) {
    if (cand && fs.existsSync(path.join(root, cand))) return cand;
  }
  return null;
}

function lastmod(src) {
  try {
    return execSync(`git log -1 --format=%cI -- ${src}`, { cwd: root }).toString().trim() || null;
  } catch {
    return null;
  }
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}
