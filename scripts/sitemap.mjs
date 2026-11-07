// Generate sitemap.xml and sitemap.txt from the built (directory-style) output.
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
  urls.push(SITE + (route === "/" ? "/" : route + "/"));
}

urls.sort();

// Generate standard XML sitemap compliant with Google Search Console
const xml =
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
  `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
  urls.map((loc) => `  <url>\n    <loc>${loc}</loc>\n  </url>`).join("\n") +
  `\n</urlset>\n`;
fs.writeFileSync(path.join(dist, "sitemap.xml"), xml, "utf-8");
console.log(`sitemap.xml: ${urls.length} urls`);

// Generate plain text sitemap (supported by Google Search Console)
const txt = urls.join("\n") + "\n";
fs.writeFileSync(path.join(dist, "sitemap.txt"), txt, "utf-8");
console.log(`sitemap.txt: ${urls.length} urls`);

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}

