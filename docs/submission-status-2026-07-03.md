# Submission status — Fri Jul 3, 2026 (deadline day)

Requirements-vs-status audit for the GMAT-on-Anki assignment. Marks: ✅ done ·
🟡 partial/needs data · 🔜 owned by another stream, in progress · ⬜ not started.

## Deadlines
- **Wed Jul 1** — both apps review one deck (no AI), a real Rust change + 3 Rust
  tests + 1 Python test, honest memory score + give-up rule, desktop installer,
  phone review session. → **met earlier.**
- **Fri Jul 3 (today)** — AI added + eval · two-way phone↔desktop sync · offline
  review. → **met** (detail below).
- **Sun Jul 5, 10:59pm CT** — proofs (calibration/Brier, held-out accuracy,
  ablation), packaged desktop + phone builds, 3–5 min demo video, Brainlift.

## Hard requirements

| # | Requirement | Status | Where / owner |
|---|---|---|---|
| 1 | Real change in Anki's **Rust** engine (not just Python UI) | ✅ | Jul 1 points-at-stake ordering + topic-mastery query; **now also `scheduler/adaptive.rs`** (Rasch θ + adaptive selection). anki `main`. |
| 2 | Desktop + phone **share one engine** and **sync two-way** | ✅ | Sync verified on real iPhone + desktop (both directions, no loss/no double, conflict rule documented). Adaptive engine ships to both via the shared rslib. |
| 3 | **Three scores** (memory / performance / readiness), each with a range | 🟡→🔜 | Memory + readiness exist (`gmat_readiness.py` + dashboard). **Performance (the 3rd) is being built by the scores chat** (`gmat_scores.rs` + `GetGmatScores` RPC + UI), consuming the shared `estimate_ability()` (θ+SE) I exposed. |
| 4 | Honest **give-up rule** (≥200 graded reviews AND ≥50% coverage) | ✅ | `gmat_readiness.py`; the scores chat mirrors per-score floors for memory/performance. |
| 5 | **Held-out evals + calibration** | 🟡 | Eval harness done (`eval_difficulty.py`, held-out Brier + accuracy vs baseline). **Needs genuine answer data** (current answers were sync-test mashing → no signal). Calibration curve is a Sunday proof. |
| 6 | **AI feature**, traceable sources, **beats a baseline**, **switchable off** | ✅/🟡 | AI difficulty calibration: 68 traceable ratings + rationale (`ai_difficulty.json`), reproducible pipeline (`calibrate_difficulty.py`). Switchable off via `BoolKey::GmatAdaptiveEnabled`. "Beats baseline" number pending real answer data (harness ready). |
| 7 | **Ablation** of one learning-science feature (on / off / plain Anki) | 🟡 | The adaptive toggle enables the three arms. Needs to be **run + written up** (Sunday). Harness/method: see `ablation` notes. |
| 8 | **AGPL-3.0 + Anki credit** | ✅ | Fork retains AGPL; NOTICE/credit in place. |

## Friday bare-minimum — confirmed done
- **Two-way sync** ✅ verified on hardware.
- **Offline review** ✅ verified (records offline, syncs on reconnect).
- **AI + eval** ✅ AI difficulty feature (traceable, toggleable) + eval harness.

## What's left for Sunday
1. **Performance score (3rd score)** — scores chat, in progress. Required for full marks. Feeds off the shared `estimate_ability()` + the `aidiff::` difficulty.
2. **Real eval numbers** — one genuine study session (miss some) → re-run `eval_difficulty.py` for a real "beats baseline" Brier, plus a calibration curve.
3. **Ablation** — run adaptive-on / adaptive-off / plain-Anki and tabulate; write up the method.
4. **Packaged builds** — desktop `.dmg` + phone app rebuilt from current `main`; regenerate the deck `.apkg` with the rebalanced answers + `aidiff::` tags.
5. **Demo video (3–5 min) + Brainlift** — not started.

## Enablers I can prep now (no collision with the scores chat's ios/bridge/gmat_scores lanes)
- Apply `aidiff::` tags so the adaptive engine + performance score use AI difficulty (currently they fall back to coarse). Done safely in a demo collection; live-collection apply deferred to when you're back.
- Validate the eval harness on synthetic data (prove it separates AI vs coarse when signal exists) — de-risks the Sunday "beats baseline" claim.
- README / method writeup for the AI + adaptive features.

## Ownership map (avoid collisions)
- **Me:** sync (ios, done) · adaptive engine (`adaptive.rs`, anki main) · AI difficulty + eval (`content/`, main) · shared `estimate_ability()`.
- **Scores chat:** `gmat_scores.rs`, `GetGmatScores` RPC, `scheduler.proto`, desktop `gmat_dashboard.py` performance display, phone score readout (`ios/`), `bridge/` score wrapper.
- **Content chat:** questions/paraphrases — **merged to main, done.**
