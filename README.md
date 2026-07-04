# GMAT Focus — Anki-based study app (desktop + phone)

A study app for the **GMAT Focus Edition**, built on a fork of [Anki](https://apps.ankiweb.net).
Desktop and phone share **one Rust engine** (Anki's `rslib`): the desktop app is the
Anki fork, and the iPhone app links the same engine through a small C-ABI bridge.

> This is a fork of Anki by Ankitects Pty Ltd and contributors, licensed **AGPL-3.0-or-later**.
> See [`anki/LICENSE`](anki/LICENSE) and [`anki/NOTICE`](anki/NOTICE) for the full license and attribution.

> **Two repositories — and why the Anki fork is a separate repo (for graders).** The desktop app is a full **fork of Anki**, a large, actively-developed **AGPL** codebase. It's kept as its **own repository** ([`graceyan212/anki`](https://github.com/graceyan212/anki)) that tracks upstream Anki rather than being vendored/copied into this repo — that keeps the GMAT diff against upstream reviewable and lets the fork still merge future Anki releases. This repo holds the iPhone app, the C-ABI bridge, the GMAT deck, and the AI/eval tooling. **Both repos share the same Rust engine**, and in **each the submission branch is `main`**:
> - **This repo** — [`graceyan212/gmat-speedrun`](https://github.com/graceyan212/gmat-speedrun): the iPhone app, the C-ABI bridge, and the GMAT deck + AI-difficulty tooling.
> - **The Anki fork** — [`graceyan212/anki`](https://github.com/graceyan212/anki): the **desktop app and the shared Rust engine** (the real engine change lives here). It is git-ignored in this repo and must be cloned separately into `anki/` (or built standalone).

## Features

- **One shared Rust engine, two apps.** The desktop app and the iPhone app run the *same* Anki `rslib` core (the phone links it through a C-ABI bridge), so the GMAT engine change ships to **both** — not a Python-only add-on.
- **Three honest GMAT scores, each with a range.** *Memory* (FSRS recall), *Performance* (a **Rasch / 1PL** ability estimate — real item-response theory), and *Readiness* (projected onto the GMAT **205–805** scale). Each carries a confidence range, and each has its own **give-up rule**: below enough evidence it *abstains* instead of showing a number it can't defend. Computed in Rust (`GetGmatScores`), so desktop and phone show identical numbers.
- **Points-at-stake review ordering.** Reviews are reordered so your **weakest GMAT topics come first**, from a per-topic mastery query over your `Section::Topic::Subtopic` tags — and it's **undo-safe** (a plain read that never disturbs Anki's undo history; covered by tests).
- **Computer-adaptive selection (Rasch / 1PL), switchable off.** Estimates your ability and serves questions near your level; **toggle-gated and off by default**.
- **Two-way offline sync.** Both apps keep a local copy so you can **review offline**, then sync **both directions** through a **self-hosted sync server** you can deploy to fly.io in minutes — see [`deploy/fly-sync/`](deploy/fly-sync/). Your data, your server (no AnkiWeb required).
- **GMAT readiness dashboard.** The three scores plus a **28-topic coverage map**, opened from the top toolbar or **Tools → GMAT Readiness**.
- **Bauhaus design throughout.** A cohesive Futura + primary-palette look across the iPhone app, the desktop dashboard, and the card reviewer — square multiple-choice markers, a green correct-answer highlight, hard-edged flat controls.

## Download the desktop app (prebuilt macOS `.dmg`)

Don't want to build from source? Grab the packaged macOS app from the
[**latest release**](https://github.com/graceyan212/gmat-speedrun/releases/latest):
download `anki-gmat-merged.dmg`, open it, and drag the app to Applications.
To build from source instead, see [Desktop app — build & run](#desktop-app--build--run) below.

## Repository layout

| Path | What it is |
|------|------------|
| `anki/` | The **desktop** app — a **separate public repository**, the Anki fork at **https://github.com/graceyan212/anki** (submission branch **`main`**). It is cloned into `anki/` here and is **not** part of this repo's clone (see the note above). Contains the GMAT engine changes: per-topic mastery (`rslib/src/scheduler/topic_mastery.rs`), the **three scores** — memory / performance / readiness (`rslib/src/scheduler/gmat_scores.rs` + the `GetGmatScores` RPC), and computer-adaptive selection (`rslib/src/scheduler/adaptive.rs`); plus the three-score readiness dashboard (`qt/aqt/gmat_dashboard.py`) and Bauhaus theme (`qt/aqt/gmat_theme.py`). |
| `bridge/` | The **C-ABI bridge** (`anki-bridge-rs`) that exposes `rslib` to Swift, plus `scripts/build-xcframework.sh` to package it as `AnkiRust.xcframework`. |
| `ios/AnkiBridgeStub/` | The **iPhone** app (SwiftUI) — imports the bundled deck, renders cards through the shared engine, and records reviews. |
| `content/` | The GMAT deck (`gmat_focus.apkg`, 108 cards), the topic taxonomy, and source items. |

## Desktop app — build & run

The desktop app is the separate Anki fork. If `anki/` isn't already present, clone it in first
(it is not included when you clone this repo):

```bash
git clone https://github.com/graceyan212/anki.git anki
```

Prereqs and full instructions live in [`anki/README.md`](anki/README.md) (rustup + Rust 1.92.0,
protoc, Ninja, Node, Python 3.10+; the repo path must contain no spaces). A clean clone builds
end-to-end with `just build` — verified.

```bash
cd anki
just run                 # build + launch the dev app
# In the app: click the "GMAT Focus" deck -> Study Now to review;
#             Tools -> GMAT Readiness for the readiness dashboard.
```

**Build the installer** (macOS `.dmg` in `anki/out/installer/dist/`):

```bash
cd anki
./tools/build-installer   # RELEASE=2 ./ninja installer
```

## iPhone app — build & run

**Prereqs:** Xcode, the Rust toolchain **1.92.0** (`rustup toolchain install 1.92.0`),
`protoc`, and `cbindgen` (`cargo install cbindgen`).

**1. Build the shared engine as an XCFramework:**

```bash
cd bridge
./scripts/build-xcframework.sh          # simulator + device
# or, faster for a simulator-only check:
SIM_ONLY=1 ./scripts/build-xcframework.sh
```

This cross-compiles `rslib` (via the bridge crate) and produces `bridge/AnkiRust.xcframework`.

**2. Open and run the app in Xcode:**

```bash
open ios/AnkiBridgeStub/AnkiBridgeStub.xcodeproj
```

- Pick an **iPhone Simulator** as the run target (the xcframework ships `arm64` device +
  `arm64` simulator slices; Apple-Silicon Macs run the simulator natively).
- Press **Run (⌘R)**. The app imports the bundled `gmat_focus.apkg` and opens into the
  review loop.

## License

AGPL-3.0-or-later, as a fork of Anki. Full license: [`anki/LICENSE`](anki/LICENSE).
Attribution and third-party notices: [`anki/NOTICE`](anki/NOTICE).
