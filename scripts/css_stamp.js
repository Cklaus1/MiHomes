#!/usr/bin/env node
/**
 * Write `static/app.css.stamp` — the hash of everything `app.css` is compiled FROM.
 *
 * SPEC-009 A13. §4.2 commits the build output so deployment stays Python-only (no Node on the
 * production path for one asset). The cost of that decision is that `app.css` can drift out of
 * date against the templates, and **the symptom appears only in production**: a class added to
 * a template works in nobody's browser, while the developer who added it sees nothing wrong
 * because their checkout has whatever CSS was last built.
 *
 * So freshness is asserted rather than trusted. `test_ui_build.py::test_css_is_current`
 * recomputes this hash and compares; a template edit that changes which utilities are used
 * turns it red until `npm run build:css && npm run stamp` runs.
 *
 * Hashing the *inputs* rather than the output is deliberate: Tailwind's output is not
 * byte-stable across versions, so an output hash would fail on a dependency bump that changed
 * nothing about this project.
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..");
const TEMPLATES = path.join(ROOT, "src", "mihomes", "web", "templates");
const INPUT_CSS = path.join(ROOT, "src", "mihomes", "web", "static", "input.css");
const CONFIG = path.join(ROOT, "tailwind.config.js");
const STAMP = path.join(ROOT, "src", "mihomes", "web", "static", "app.css.stamp");

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : full.endsWith(".html") ? [full] : [];
  });
}

/** Sorted so the hash does not depend on filesystem ordering across machines. */
const sources = [...walk(TEMPLATES).sort(), INPUT_CSS, CONFIG];

const h = crypto.createHash("sha256");
for (const file of sources) {
  // Relative POSIX path, so a Windows checkout and a Linux CI agree.
  h.update(path.relative(ROOT, file).split(path.sep).join("/"));
  h.update(fs.readFileSync(file));
}

const digest = h.digest("hex");
fs.writeFileSync(STAMP, digest + "\n", "utf8");
console.log(`app.css.stamp <- ${digest.slice(0, 16)}… (${sources.length} source files)`);
