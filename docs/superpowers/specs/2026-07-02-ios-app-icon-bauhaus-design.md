# GMAT Review — Bauhaus App Icon

**Date:** 2026-07-02
**Status:** Design approved via rendered mockups; ready for implementation plan
**Scope:** Add a home-screen app icon for the iOS review app (`ios/AnkiBridgeStub`)
in the established Bauhaus language. Presentation only — no Swift, Rust/FFI, or
deck changes.

Related: [Bauhaus UI Redesign](./2026-07-01-anki-bauhaus-ui-redesign-design.md)
(source of the locked palette, typography, and geometry this icon reuses).

---

## 1. Goal

The app currently ships **no icon** — there is no asset catalog in the Xcode
project (`ASSETCATALOG_COMPILER_GENERATE_ASSET_SYMBOLS = NO`, no
`AppIcon.appiconset`), so it shows the blank gray system placeholder on the home
screen. Give it a distinctive icon that reads as part of the same Bauhaus family
as the redesigned review UI, and that stays legible from 1024px down to the
smallest home-screen size.

Success = the app shows a cohesive Bauhaus icon on the phone after a rebuild;
the build produces no missing-/malformed-icon warnings; no code paths change.

---

## 2. The design (locked): "Rating Spectrum"

Chosen from three rendered concepts (A — the three-shape mark on paper; B — a
Futura "G" monogram on ink; **C — Rating Spectrum**). C won: it is the most
vivid on a crowded home screen and it encodes the app's defining interaction,
the four-color rating spectrum.

**Composition (full-bleed 1024×1024 square):**
- Four hard-edged quadrants, in reading order = the rating spectrum:
  - top-left **Red `#E2231A`** (Again)
  - top-right **Yellow `#F2C200`** (Hard)
  - bottom-left **Green `#2E9E4F`** (Good)
  - bottom-right **Blue `#1E52A8`** (Easy)
- Quadrants separated by a thick **Ink `#1A1A1A`** cross (grid rule ≈ 2.6% of
  the icon edge), echoing the section rules used throughout the UI.
- Centered **Ink disc** (radius ≈ 20.5% of the edge) holding a paper-colored
  **Futura "G"** — carries the GMAT identity and anchors the composition.
- Background behind the grid rules is Ink, so the cross reads as solid ink.

**Constraints (inherited from the locked language):**
- Only the six palette tokens: Red, Yellow, Green, Blue, Ink, Paper `#F5F1E6`.
- Flat, saturated fills. **No gradients, gloss, shadows, or pre-rounded corners**
  — iOS applies the home-screen squircle mask itself; the master is a plain
  opaque square.
- Green is the one deliberate non-primary, consistent with the UI's rating
  spectrum (documented exception in the UI redesign spec, §6).

Rendered master and a size-legibility check are saved at
`ios/icon-mockups/C-final-1024.png` and `C-final-sizes.png`. The "G" stays
legible to ~87px; below that the four-color composition still reads.

---

## 3. Architecture & components

Everything is asset + project-configuration; there is no runtime code.

### 3.1 Icon generator (reproducible)
- A small committed Python/Pillow script (target: `ios/AnkiBridgeStub/icon/generate_icon.py`)
  draws the master deterministically from the palette tokens and the system
  Futura font (`/System/Library/Fonts/Supplemental/Futura.ttc`), rendered at 3×
  supersample and downscaled with LANCZOS for clean edges.
- Output: a single opaque **1024×1024 RGB** PNG (no alpha — App Store / asset
  catalog requirement for the marketing size). Keeping the generator in the repo
  means the icon can be regenerated or tweaked without re-deriving the geometry.

### 3.2 Asset catalog
- Create `ios/AnkiBridgeStub/AnkiBridgeStub/Assets.xcassets` with an
  `AppIcon.appiconset` using the modern **single-size** iOS format: one 1024×1024
  image, `"platform": "ios"`, Xcode derives the rest. (iOS 17 deployment target
  supports this; no need to enumerate every legacy size.)

### 3.3 Xcode project wiring (`project.pbxproj`)
- Add the `Assets.xcassets` file reference to the project, into the app group and
  the app target's **Resources** build phase.
- Set `ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon` in **both** the Debug and
  Release build configurations of the `AnkiBridgeStub` target.
- Leave `ASSETCATALOG_COMPILER_GENERATE_ASSET_SYMBOLS` as-is (or enable it — not
  required for the icon to work).

No changes to `Info.plist` are required for a modern asset-catalog icon.

---

## 4. Testing / verification

- **Build:** the app compiles for the simulator/device (`xcodebuild` or Xcode)
  with **no missing/invalid app-icon warnings**.
- **Asset present:** `AppIcon.appiconset/Contents.json` references the 1024 PNG;
  the PNG is exactly 1024×1024, opaque, no alpha.
- **Visual on device/simulator:** the home-screen icon is the Rating Spectrum,
  not the gray placeholder; the squircle mask is applied cleanly by iOS.
- **Small-size sanity:** icon is recognizable in Settings/Spotlight sizes (the
  size-check sheet already confirms this at 87–180px).
- **Regression:** app still launches and the review loop is unaffected (this is a
  resource/config change only).

---

## 5. Housekeeping

- `ios/icon-mockups/` currently holds the exploration mockups (A, B, C previews)
  plus the final C. Keep `C-final-*.png` as design reference; the losing A/B
  concept previews can be deleted once the icon lands (optional).

---

## 6. Out of scope

- **Launch/splash screen.** A matching Bauhaus launch screen was offered and
  deferred; icon only for now. Additive later.
- Any Swift, Rust/FFI, scheduling, deck, or note-template changes.
- macOS/desktop icon (the desktop fork is a separate track; see the desktop
  design spec).
- Alternate icons / dark-variant icon.
