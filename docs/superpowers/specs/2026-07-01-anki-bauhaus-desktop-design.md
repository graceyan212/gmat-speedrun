# GMAT Review — Bauhaus Desktop Extension

**Date:** 2026-07-01
**Status:** Dashboard mockup approved; card-look port gated on the final iOS card CSS
**Scope:** the desktop Anki fork at `~/Desktop/alpha/speedrun/anki/` (branch `gmat-build`
→ new branch `bauhaus-desktop`). Restyle **only** the GMAT-specific surfaces — stock
Anki's own windows and theming are untouched.
**Companion spec:** [2026-07-01-anki-bauhaus-ui-redesign-design.md](2026-07-01-anki-bauhaus-ui-redesign-design.md) (iOS).

---

## 1. Goal

Extend the approved Bauhaus language to the two **GMAT desktop surfaces** so the phone
and desktop read as one product:

1. The **readiness dashboard** (the score screen — Tools → GMAT Readiness).
2. The **GMAT cards** as rendered in the **desktop reviewer**.

Explicit non-goal: reskinning stock Anki. Anki is a large upstream app; a global theme
overhaul would fight every Anki update and is out of scope.

## 2. Shared design tokens

Identical to the iOS spec: **Futura**; paper `#F5F1E6`; ink `#1A1A1A`; red `#E2231A`,
yellow `#F2C200`, green `#2E9E4F`, blue `#1E52A8`; hard-edged **square markers**;
**green = covered / correct**; flat fills, no gradients/gloss/shadows; **light only**.

## 3. Readiness dashboard — `qt/aqt/gmat_dashboard.py`

Approved mockup covers **both** states the dialog already has:
- **SCORE** — score `/100`, likely range, three evidence stats, the 28-topic coverage
  map, and the give-up rule.
- **ABSTAIN** — "not enough data yet" (fires below 200 graded reviews or 50% coverage),
  the failing stats flagged, and a "what's left" checklist.

**Current tech:** a `QDialog` with a headline `QLabel` (pt 20 bold), a subhead `QLabel`,
a `QTextBrowser` body (inline HTML), and a Close `QDialogButtonBox`. Registered on
`gui_hooks.main_window_did_init`, adds one "GMAT Readiness" action to the Tools menu.

**Restyle approach (presentation only — no logic change):**
- **Dialog QSS:** paper background, Futura default font, ink text.
- **Headline `QLabel`:** Futura, large/bold. SCORE → the big score `/100` in ink;
  ABSTAIN → "NOT ENOUGH DATA YET" with the yellow caution accent (replaces the current
  amber `#b58900`).
- **Rewrite `_body_html`** to emit Bauhaus HTML/CSS within `QTextBrowser`'s rich-text
  subset:
  - evidence stats as a bordered table (big tabular numbers + uppercase labels);
  - exam-coverage map: section names in bold uppercase; each topic a small **square
    marker** — green filled = covered, hollow/muted = not covered;
  - give-up rule in a left-ruled note.
- **Score bar:** approximate within `QTextBrowser` (a colored table-cell bar) or a bold
  range line.

**Fidelity caveat:** `QTextBrowser` supports only a subset of CSS (tables + basic inline
styles; **no** flexbox/grid). The result will closely approximate the mockup; the score
bar is the most likely simplification. The headline and window chrome are Qt (font + a
little QSS), the body is HTML we fully control.

**Unchanged:** all content and both states; the scoring logic in
`pylib/anki/gmat_readiness.py` and `rslib/.../topic_mastery.rs` is **not** touched.

## 4. Card reviewer look — deferred until the final iOS card CSS lands

Port the card visual language to the **desktop reviewer** by setting the **GMAT
note-type CSS** (scoped to the deck) — **not** the app-wide SCSS/theme (`_vars.scss`,
`reviewer.scss`), which would fight Anki updates and affect non-GMAT cards.

- Pure CSS delivers the typography, palette, paper background, answer rule, and the
  green correct-answer treatment.
- The **square letter-markers** need one of: (a) a small JS transform placed in the card
  **template** — which then works on desktop **and** web **and** iOS from a single place
  (the DRY option; could later replace the iOS webview transform), or (b) restructuring
  the deck's choices into structured HTML so plain CSS can box them. Decide when
  implementing.
- This change lives in the deck/note-type definition (regenerate the note-type CSS /
  template), so it reaches desktop, web, and Android — all clients that open the deck.

Sequenced after the iOS card CSS is final so the two stay byte-consistent where possible.

## 5. Build & verify

Use the fork's **`just` recipes** (per its `CLAUDE.md`) — do **not** call `./ninja`,
`./run`, or `tools/` scripts directly:
- Dashboard (Python/Qt): `just lint` (mypy/ruff) after edits; live visual check via
  `just run` → Tools → GMAT Readiness.
- Card CSS: visual check in the desktop reviewer via `just run`.

## 6. Out of scope

- App-wide Anki theming (QSS/SCSS palette overhaul), dark mode, stock Anki windows.
- The Rust/Python **scoring logic** (presentation layer only).
- The installer / packaging (tracked separately in the project status).
