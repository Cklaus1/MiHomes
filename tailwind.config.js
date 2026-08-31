/**
 * SPEC-009 Step 2 (D5, A12/A14).
 *
 * The palette moved here from the inline `tailwind.config` block that lived in
 * `base.html:10-19` under the play CDN. It is now the single source for brand colour
 * (A14) — a template naming a raw hex bypasses it, which `test_ui_tokens.py` forbids.
 *
 * **The `content` globs are load-bearing in a way they were not under the CDN.** The play
 * CDN ran the JIT compiler in the browser, so any class a template named simply worked.
 * A compiled build inverts that: a class these globs do not see does not exist in the
 * stylesheet, and the failure is silent — no style, in production, having looked correct
 * in development where nothing was compiled.
 *
 * Hence a recursive glob rather than a top-level one: `partials/`, `settings/`, `team/` and
 * `onboarding/` all hold real markup.
 * `test_ui_build.py::test_content_globs_cover_every_template` asserts every template the audit
 * enumerates is actually matched.
 *
 * (The recursive glob is deliberately described rather than quoted in this comment: a literal
 * double-star-slash inside a block comment terminates it, which is exactly how the first
 * version of this file failed to parse.)
 */
module.exports = {
  content: ["./src/mihomes/web/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        // Verbatim from base.html:14 — moving it must not restyle the app.
        brand: {
          50: "#f0f9ff",
          100: "#e0f2fe",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
        },
      },
    },
  },
  plugins: [],
};
