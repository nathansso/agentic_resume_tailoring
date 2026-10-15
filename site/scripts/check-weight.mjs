// Fails when the home page weighs 2 MB or more (issue #275).
//
//   npm run check:weight      (after `npm run build`)
//
// Counts dist/index.html plus every same-origin file it references: stylesheets, scripts,
// images (the largest srcset candidate, lazy ones included) and every font a stylesheet
// declares. Raw bytes, no compression credit, so the real transfer is smaller. The demo is
// built from text and still images, so there is no video to add.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const BUDGET = 2_000_000;
const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");
const html = readFileSync(join(dist, "index.html"), "utf8");

const files = new Map([["index.html", statSync(join(dist, "index.html")).size]]);

function add(ref, from = "") {
  if (!ref || /^(https?:)?\/\//.test(ref) || ref.startsWith("data:") || ref.startsWith("#")) return;
  const clean = ref.split("#")[0].split("?")[0];
  const rel = clean.startsWith("/") ? clean.slice(1) : normalize(join(dirname(from), clean));
  const path = join(dist, rel);
  if (!existsSync(path) || files.has(rel) || !statSync(path).isFile()) return;
  files.set(rel, statSync(path).size);
  if (rel.endsWith(".css")) {
    for (const m of readFileSync(path, "utf8").matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)) add(m[1], rel);
  }
}

for (const m of html.matchAll(/<(?:link|script|img|source|video)\b[^>]*>/g)) {
  const tag = m[0];
  for (const attr of ["href", "src", "poster"]) {
    const v = tag.match(new RegExp(`\\b${attr}="([^"]+)"`));
    if (v && !(attr === "href" && !/rel="(stylesheet|preload|modulepreload|icon)"/.test(tag))) add(v[1]);
  }
  const srcset = tag.match(/\bsrcset="([^"]+)"/);
  if (srcset) srcset[1].split(",").forEach((c) => add(c.trim().split(/\s+/)[0]));
}
// Inline module scripts' imports and CSS bundles referenced from <astro-island> or inline data.
for (const m of html.matchAll(/["'](\/_astro\/[^"']+)["']/g)) add(m[1]);

const total = [...files.values()].reduce((a, b) => a + b, 0);
for (const [name, size] of [...files].sort((a, b) => b[1] - a[1])) {
  console.log(`${String(size).padStart(9)}  ${name}`);
}
console.log(`${String(total).padStart(9)}  total (budget ${BUDGET})`);
if (total >= BUDGET) {
  console.error("The home page is over its 2 MB budget.");
  process.exit(1);
}
