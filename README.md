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
- **Three honest GMAT scores, each with a range.** *Memory* (FSRS recall), *Performance* (a **Rasch / 1PL** ability estimate — real item-response theory), and *Readiness* (projected onto the GMAT **205–805** scale). Each carries a confidence range, and each has its own **give-up rule**: below enough evidence it *abstains* instead of showing a number it can't defend. Computed in Rust (`GetGmatScores`), so desktop and phone show identical numbers. → Full methodology (formulas, thresholds, code refs): [`docs/scores/`](docs/scores/).
- **Points-at-stake review ordering.** Reviews are reordered so your **weakest GMAT topics come first**, from a per-topic mastery query over your `Section::Topic::Subtopic` tags — and it's **undo-safe** (a plain read that never disturbs Anki's undo history; covered by tests).
- **Computer-adaptive selection (Rasch / 1PL), switchable off.** Estimates your ability and serves questions near your level; **toggle-gated and off by default**.
- **Confidence-based grading + calibration (AI).** Tap your choice, then how sure you are — **Guessing / Fairly sure / Confident**. The shared engine turns *correctness × confidence* into Again/Hard/Good/Easy (**calibration, not speed** — time is confounded by reading and computation load, so it never grades), and flags the **confident-but-wrong** miss that hurts most on an adaptive test. Response time is shown for **pacing** — with a **Flag & skip** "cut your losses" move — and the session recaps how calibrated you were. Computed in Rust (`grade_answer`, `GradeAnswer` RPC), so both apps grade identically; manual buttons stay as an override. **On by default** on both (toggle it off in iPhone settings or desktop Preferences).
- **Two-way offline sync.** Both apps keep a local copy so you can **review offline**, then sync **both directions** through a **self-hosted sync server** you can deploy to fly.io in minutes — see [`deploy/fly-sync/`](deploy/fly-sync/). Your data, your server (no AnkiWeb required).
- **GMAT readiness dashboard.** The three scores plus a **28-topic coverage map**, opened from the top toolbar or **Tools → GMAT Readiness**.
- **Bauhaus design throughout.** A cohesive Futura + primary-palette look across the iPhone app, the desktop dashboard, and the card reviewer — square multiple-choice markers, a green correct-answer highlight, hard-edged flat controls.

## Rubric coverage map — where everything is

**Exam:** GMAT Focus Edition (total **205–805**; sections Quant / Verbal / Data Insights). Every rubric requirement, with a one-click link to its evidence and the command to reproduce it. (`anki/…` links are in the [Anki fork](https://github.com/graceyan212/anki); the rest are in this repo.)

| Rubric item | Evidence | Reproduce |
|---|---|---|
| **Real Rust engine change** (§2, 7a) | [`WHY_RUST.md`](https://github.com/graceyan212/anki/blob/main/WHY_RUST.md) · [`FILES_TOUCHED.md`](https://github.com/graceyan212/anki/blob/main/FILES_TOUCHED.md) · engine: [`topic_mastery.rs`](https://github.com/graceyan212/anki/blob/main/rslib/src/scheduler/topic_mastery.rs), [`gmat_scores.rs`](https://github.com/graceyan212/anki/blob/main/rslib/src/scheduler/gmat_scores.rs), [`adaptive.rs`](https://github.com/graceyan212/anki/blob/main/rslib/src/scheduler/adaptive.rs) | `cd anki && cargo test -p anki --lib` (35 Rust tests) · `pytest pylib/tests/test_topic_mastery.py test_gmat_scores.py test_topic_breakdown.py` (Python→engine) |
| **Three scores + range + give-up rule** (§4, §9) | [`docs/MODELS.md`](docs/MODELS.md) (memory / performance / readiness, each give-up rule) · [`gmat_scores.rs`](https://github.com/graceyan212/anki/blob/main/rslib/src/scheduler/gmat_scores.rs) `GetGmatScores` | readiness dashboard / phone scores page |
| **Memory calibration + performance eval, held-out** (§9) | [`docs/RESULTS.md`](docs/RESULTS.md) §2–3 (Brier, reliability diagram) | `python3 content/tools/eval_difficulty.py` |
| **AI traceable to a source + beats a baseline** | [`docs/RESULTS.md`](docs/RESULTS.md) §2 · [`docs/ai-adaptive-feature.md`](docs/ai-adaptive-feature.md) · [`docs/ai-retrieval-feature.md`](docs/ai-retrieval-feature.md) | `python3 content/tools/eval_difficulty.py` |
| **Study-feature ablation (feature on/off/plain)** (§8) | [`docs/ABLATION.md`](docs/ABLATION.md) | `python3 content/tools/ablation.py` |
| **7b — sync: no loss, no double-count, conflict rule** | [`docs/SYNC_TEST.md`](docs/SYNC_TEST.md) | `PYTHONPATH=anki/out/pylib:anki/out/qt anki/out/pyenv/bin/python content/scripts/sync_conflict_test.py` |
| **7c — coverage map + abstain rule** | [`content/taxonomy.md`](content/taxonomy.md) (28-topic outline) · abstain in [`gmat_scores.rs`](https://github.com/graceyan212/anki/blob/main/rslib/src/scheduler/gmat_scores.rs) · dashboard "TOPICS PRACTICED — N/28" | readiness dashboard |
| **7d — paraphrase (Performance ≠ Memory)** | [`docs/PARAPHRASE_TEST.md`](docs/PARAPHRASE_TEST.md) — gap **−15.5 pts** | `PYTHONPATH=anki/out/pylib anki/out/pyenv/bin/python content/scripts/memory_vs_performance.py content/responses.json` |
| **7e — leakage near-copy scan (clean)** | [`docs/RESULTS.md`](docs/RESULTS.md) §2.1.1 · [`content/tools/leakage_scan.py`](content/tools/leakage_scan.py) — **0 leaks** | `python3 content/tools/leakage_scan.py` |
| **7f — AI card check (3 counts + cutoff)** | [`docs/AI_CARD_CHECK.md`](docs/AI_CARD_CHECK.md) — **32 useful / 0 wrong / 18 blocked** | `python3 content/tools/check_cards.py` |
| **7g — crash (20×) + offline still scores** | [`docs/CRASH_AND_OFFLINE.md`](docs/CRASH_AND_OFFLINE.md) — **20/20 clean** | `… content/scripts/crash_test.py` · `… offline_test.py` |
| **7h — one-command 50k benchmark (p50/p95/worst)** | [`anki/docs/BENCHMARK.md`](https://github.com/graceyan212/anki/blob/main/docs/BENCHMARK.md) — all §10 targets pass | `cd anki && just bench` |
| **Prompt-injection resistance** | [`content/tools/injection_test.py`](content/tools/injection_test.py) (23 checks) · [`docs/ai-adaptive-feature.md`](docs/ai-adaptive-feature.md) §1a | `python3 content/tools/injection_test.py` |
| **Runs with AI off / offline; give-up rule** | [`docs/CRASH_AND_OFFLINE.md`](docs/CRASH_AND_OFFLINE.md) · [`docs/MODELS.md`](docs/MODELS.md) | `… content/scripts/offline_test.py` |
| **AGPL-3.0 + Anki credit** | [`anki/LICENSE`](anki/LICENSE), [`anki/NOTICE`](anki/NOTICE) | — |

## Evals & tests

Honest, reproducible evaluation results — every number comes from a deterministic
run on disk — are collected in [`docs/RESULTS.md`](docs/RESULTS.md). Highlights:

- **Difficulty eval + leakage check** — does AI-rated difficulty predict answers,
  with train/test hygiene ([`docs/RESULTS.md`](docs/RESULTS.md) §2–2.1).
- **Train/test near-copy scan (rubric 7e)** — a text-similarity scan
  (`content/tools/leakage_scan.py`, stdlib only) of every training item against
  every test item; result is **clean** — 0 verbatim test items or copies leaked
  into train ([`docs/RESULTS.md`](docs/RESULTS.md) §2.1.1).
- **Calibration** — reliability diagram for the Rasch model ([§3](docs/RESULTS.md)).
- **Adaptive-study ablation** — simulated A/B/C over item-selection policies
  ([`docs/ABLATION.md`](docs/ABLATION.md)).
- **Paraphrase test (rubric 7d) — is Performance just copying Memory?** For 29
  cards with 83 reworded questions, recall on the memorized card (86.8%) vs
  accuracy on the paraphrases (71.3%) gives a **−0.155 gap** → Performance is
  *not* an echo of Memory. Labelled simulation; full method, numbers, and
  reproduce command in [`docs/PARAPHRASE_TEST.md`](docs/PARAPHRASE_TEST.md).
- **Sync test (rubric 7b) — no reviews lost, none double-counted, clear conflict
  winner.** 10 cards reviewed offline on device A + 10 different on device B →
  after sync **all 20 land once each on both devices** (0 lost, 0 duplicated).
  Then the **same** card reviewed differently on both → both reviews are kept in
  history and the card converges to a single clear winner (last write wins). Runs
  against the fork's real Anki sync server; full method, numbers, the written
  conflict rule, and reproduce command in [`docs/SYNC_TEST.md`](docs/SYNC_TEST.md).
- **AI-generated card quality (rubric 7f) — are the generated cards any good?**
  A **50-item gold set** of known-correct GMAT answers (`content/gold_set.json`),
  cards generated from **one real source** (`content/sources/percents_and_ratios.md`)
  via the same LLM backend as the difficulty tooling (injection-hardened), and a
  **checker** (`content/tools/check_cards.py`) that sorts every card into three
  counts — *correct&useful / wrong-fact / bad-teaching (vague·trivial·duplicate)* —
  against a **cutoff fixed before the results** (block wrong-fact/bad-teaching,
  require ≥80% pass rate). Full method + the three counts in
  [`docs/AI_CARD_CHECK.md`](docs/AI_CARD_CHECK.md).
- **Crash & offline safety (rubric 7g)** — killing the app mid-review **20×** in
  a row leaves **20/20 clean, 0 corrupted** collections (SQLite WAL +
  transactions), and with the **network pulled** the shared engine still grades,
  scores, selects cards, and breaks down topics — the AI difficulty feature is
  dev-time-only and degrades to the coarse `difficulty::` tag. Both tests run at
  the engine level, which covers desktop **and** phone (they share one rslib +
  SQLite core). Method, results, and reproduce commands in
  [`docs/CRASH_AND_OFFLINE.md`](docs/CRASH_AND_OFFLINE.md)
  (`content/scripts/crash_test.py`, `content/scripts/offline_test.py`).

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
| `content/` | The **108-card starter deck** (`gmat_focus.apkg`) you import to try the app; the **369-item source bank** (`items.json`, of which 68 are finely AI-rated) that feeds the AI difficulty / retrieval / eval tooling; the topic taxonomy (`taxonomy.md`); and the AI difficulty ratings. |

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
