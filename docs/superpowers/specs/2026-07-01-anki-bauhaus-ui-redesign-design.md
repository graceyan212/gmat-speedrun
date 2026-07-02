# GMAT Review — Bauhaus UI Redesign

**Date:** 2026-07-01
**Status:** Design approved via visual mockups; ready for implementation plan
**Scope:** Visual redesign of the iOS review app — both the SwiftUI shell and the
in-card HTML/CSS rendering. No changes to the Rust bridge or scheduling logic.

---

## 1. Goal

The review app (`ios/AnkiBridgeStub`) works but looks unstyled: default iOS
`.borderedProminent` buttons, a plain system-font header, and — the bigger
problem — card content rendered as raw HTML (the deck ships **empty CSS**), so
questions appear as default-font text with `<br>`-separated multiple-choice
options and a plain `<hr>` before the answer.

Redesign the whole surface in a **full Bauhaus** visual language: geometric
sans typography, the Bauhaus primary palette, hard-edged geometry, and a
strong, legible information hierarchy suited to long, focused study sessions.

Success = the reviewer looks intentional and cohesive end-to-end, the
multiple-choice questions are markedly easier to read, and the four rating
buttons communicate their meaning through an intuitive color spectrum — with
**no changes to the Rust/FFI layer** (ships as a pure Swift + CSS/JS change).

---

## 2. Design language (locked)

### Typography
- **Futura** throughout — the original Bauhaus geometric sans, available as a
  system font on iOS (usable in both SwiftUI via `Font.custom("Futura", …)` and
  in the WKWebView via `font-family: Futura`).
- Fallback stack: `"Futura", "Futura-Medium", "Avenir Next", -apple-system, sans-serif`.
- Usage:
  - **Bold + letter-spaced UPPERCASE** for labels, buttons, tabs, the wordmark.
  - **Medium weight** for question stems and choice text.
  - **Regular** for explanation body copy.
- Counters use tabular figures (`font-variant-numeric: tabular-nums` /
  `.monospacedDigit()`).

### Palette
| Token | Hex | Use |
|-------|-----|-----|
| Red | `#E2231A` | "Again" rating; header circle mark |
| Yellow | `#F2C200` | "Hard" rating (**white label text** — see note); header triangle mark |
| Green | `#2E9E4F` | "Good" rating; **correct-answer highlight** |
| Blue | `#1E52A8` | "Easy" rating; header square mark; topic-label bullet |
| Ink (near-black) | `#1A1A1A` | text, rules, borders, "Show Answer" bar |
| Paper (warm off-white) | `#F5F1E6` | app + card background |

- Flat, saturated fills. **No gradients, no gloss, no drop shadows, no rounded
  button corners.** (The phone bezel is rounded; the UI inside is hard-edged.)
- **Rating labels are white on all four buttons** (red/yellow/green/blue), for a
  uniform label treatment — an explicit product decision by the deck owner.
  Recorded tradeoff: white on the bright yellow "Hard" button is low-contrast
  (~1.5:1, below accessibility thresholds) and will look faint in bright light or
  on some displays. Accepted in favor of visual consistency. Reverting "Hard" to
  black text — or darkening the yellow to gold (`#A07500`) — is a one-line change
  if revisited.
- All other text is ink on paper.

### Geometry
- Multiple-choice markers: **hard-edged squares**, `2.5px` ink border, letter
  centered inside.
- Header mark: red **circle** + blue **square** + yellow **triangle**.
- Thick ink rules (`5px`) as section breaks; a `3px` ink rule under the header.

### The rating spectrum (locked)
Ratings read left→right as a felt "how well did I know it" spectrum. The key
semantic: **Again is the only failure**; Hard/Good/Easy are all "correct,"
ranked by effort.

| Button | Meaning | Color | Label text |
|--------|---------|-------|------------|
| **Again** | Failed / couldn't recall | Red `#E2231A` | white |
| **Hard** | Correct but a struggle | Yellow `#F2C200` | white |
| **Good** | Correct, normal effort | Green `#2E9E4F` | white |
| **Easy** | Correct, effortless | Blue `#1E52A8` | white |

Because red now means "failure," the **correct-answer highlight is green**, so
red is never ambiguous.

---

## 3. Screen-by-screen design

### 3.1 Header (all states)
- Left: the circle/square/triangle mark + `GMAT` wordmark (Futura bold).
- Right: answered count as tabular Futura (e.g. `012`).
- `3px` solid ink bottom border.
- **Progress bar** (`6px`, red fill over paper track): **deferred** — a
  meaningful fraction needs a "total due" count that the bridge does not expose
  today (see §5). v1 ships the bold count without a denominator or bar.

### 3.2 Question state
- Topic label: small uppercase, letter-spaced, with a blue square bullet
  (e.g. `■ QUANT · PERCENTS`). **Deferred** unless the topic is available
  without a bridge change (see §5); omit the label in v1 if not.
- Stem: Futura medium, ~19px, line-height ~1.4, ink.
- Choices: vertical list; each row = square letter-marker + choice text.
- Footer: full-width **SHOW ANSWER** bar — solid ink background, white
  uppercase letter-spaced Futura, hard corners. A `3px` ink rule sits directly
  above the bar (matching the dividers used between the rating buttons).

### 3.3 Answer state
- Same stem + choice list, but the correct choice is highlighted: **green-filled
  marker, green box outline, green "ANSWER" flag**.
- A `5px` ink rule, then an ink **EXPLANATION** tab label, then the explanation
  body (Futura regular, readable line-height).
- Footer: the four **rating buttons** in a single row, `2px` ink gaps between
  them (grid feel), hard edges, colored per the spectrum table above. A `3px` ink
  rule runs across the **top** of the row too, so the row reads as one enclosed
  grid rather than four floating blocks.
- Per-button interval hints (e.g. `<1m / 8m / 1d / 4d`): **deferred** — the
  bridge does not return next-interval previews today (see §5).

### 3.4 Loading state
- Paper background; centered geometric motif (the circle/square/triangle mark)
  with a Futura uppercase caption (e.g. `LOADING DECK…`). Replaces the default
  `ProgressView` styling with something on-brand (a system spinner may remain
  underneath if simplest).

### 3.5 Finished state
- Paper background; a bold geometric composition — a large primary shape and
  `SESSION COMPLETE` in Futura bold uppercase, with the answered count.

### 3.6 Error state
- Keep the monospaced, selectable error text for debuggability, but frame it in
  the Bauhaus shell (paper background, ink `ERROR` tab label, Futura heading).

---

## 4. Architecture & components

Two files change; responsibilities stay cleanly separated.

### 4.1 `CardWebView.swift` — the card (biggest visual win)
The rendered card HTML from rslib is raw and inline:

- **Question HTML:** `stem<br><br>A) …<br>B) …<br>C) …<br>D) …<br>E) …`
- **Answer HTML:** the same front, then `<hr id="answer">`, then
  `<b>Answer:</b> C<br><br><b>Explanation:</b> …`

Pure CSS can't turn inline `A) …<br>` text into square markers or highlight the
correct choice, so `fullDocument` gains **(a) a Bauhaus stylesheet and (b) a
small, self-contained JS transform** that runs on load:

1. Split the body on `<hr id="answer">` into `front` / `back` (back may be absent
   in the question state).
2. In `front`, split on `<br>`; the leading text before the first line matching
   `/^\s*([A-E])[).]/` is the **stem**; each matching line becomes a **choice**
   (`{letter, text}`) rendered as `marker + text`.
3. If `back` exists: read the answer letter via `/Answer:\s*<\/b>?\s*([A-E])/`,
   add the `correct` treatment to the matching choice, extract the text after
   `Explanation:` and render it in the explanation block under the ink rule/tab.
4. **Fallback:** if the choice pattern doesn't match (e.g. a Basic front/back
   *memory* card with no A–E options), render the original HTML under the base
   Bauhaus typography — no crash, still styled. This also covers plain
   front/back recall cards gracefully.

The WKWebView `body` background becomes **paper** (`#F5F1E6`) rather than clear,
so the card matches the shell. Existing deck CSS (currently empty) is still
appended after our styles so any future deck CSS can override.

### 4.2 `ContentView.swift` — the SwiftUI shell
- Replace `.borderedProminent` buttons with custom, reusable Bauhaus button
  styles: a `BauhausBlockButton` (flat fill, hard corners, uppercase
  letter-spaced Futura label, per-rating color) for the ratings and the
  Show-Answer bar.
- Restyle `header`, and the `loading` / `finished` / `error` branches per §3.
- Add a small **`BauhausTheme`** helper (colors, a `futura(_:weight:)` font
  helper, spacing constants) so both files and all states share one source of
  truth and no magic values are scattered around.
- Set the screen background to paper and pin the appearance to **light** (see
  §6, dark mode).

No changes to `ReviewViewModel`, `AnkiEngine`, `AnkiBridge`, the Rust crate, or
the `.apkg`.

---

## 5. Deferred (nice-to-haves that require bridge/data work)

These appear in the mockups but need data the app doesn't currently have. They
are **explicitly out of scope for v1** so the redesign ships as a pure
Swift/CSS/JS change with no Rust rebuild. Each degrades gracefully (simply
omitted) until added later:

1. **Topic label** (`QUANT · PERCENTS`) — needs the note's topic tag surfaced
   through the bridge (`nextCard` doesn't return tags today).
2. **Progress denominator + bar** (`012 / 108`) — needs a "total due / total in
   deck" count from rslib.
3. **Per-rating interval hints** (`<1m / 8m / 1d / 4d`) — needs the scheduler's
   next-interval preview exposed through the bridge.

v1 header shows the answered count alone; v1 cards omit the topic label and
interval hints. Adding any of these later is additive and won't disturb the
visual language.

---

## 6. Decisions & trade-offs

- **Full Bauhaus over Swiss** (user's call): more expressive; primary palette
  maps naturally onto the four rating buttons.
- **Rating spectrum breaks strict primary purity** (green is secondary): a
  deliberate exception — for functional judgment signals, intuitive meaning
  beats palette purity. The rest of the app stays on the primaries.
- **White label on bright yellow "Hard"** — the deck owner's explicit decision,
  overriding the contrast recommendation, for uniform white text across all four
  ratings. Tradeoff recorded in §2 (Palette): ~1.5:1 contrast, faint outside
  ideal viewing conditions. One-line revert available if revisited.
- **Dark mode:** the Bauhaus paper aesthetic is inherently light. v1 **pins the
  app to light appearance** and drops the `color-scheme: light dark` hint in the
  card document, so paper/ink render consistently regardless of system setting.
- **JS transform over template changes:** we reshape the *rendered* HTML in the
  webview rather than editing the deck's note templates, because the deck/tag
  layout is a shared downstream contract that must not change.

---

## 7. Testing / verification

- **Build:** the iOS app compiles and launches in the simulator.
- **Headless self-test:** `ANKI_SELFTEST=1` still drives the review loop
  end-to-end (unchanged; it doesn't touch the UI).
- **Visual, question state:** stem + five square-markered choices + ink
  SHOW ANSWER bar, on paper, in Futura.
- **Visual, answer state:** correct choice highlighted green, ink rule +
  EXPLANATION block, four rating buttons (red / yellow-with-**white** / green /
  blue) with 2px gaps between and a 3px ink rule across the top of the row.
- **Fallback:** a Basic front/back memory card (no A–E options) renders styled
  and readable via the fallback path, no layout breakage.
- **States:** loading, finished, and error screens all render in the Bauhaus
  shell.
- **Regression:** answering still advances the queue and records reviews (the
  engine path is untouched).

---

## 8. Out of scope

- Rust bridge / FFI / scheduling changes (see §5 for what that would unlock).
- Deck content, note templates, tags, or the `.apkg`.
- New review features (undo, deck picker, stats, settings).
- Dark-mode Bauhaus variant.
- **Desktop / macOS.** This app is iPhone/iPad only (`SDKROOT = iphoneos`) and
  the engine is compiled for iOS arches only. A "Bauhaus desktop" — either
  porting the card CSS into the deck so other Anki clients render it, or a full
  macOS build of this app (needs a macOS engine build + Mac target) — is a
  separate future project, not part of this redesign.
