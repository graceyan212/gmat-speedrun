# Three scores (memory / performance / readiness) in the shared engine — desktop + phone

**Date:** 2026-07-03
**Status:** Design approved (brainstorm), pending spec review
**Assignment track:** Fri Jul 3 — Mobile: *"The phone shows the three scores with ranges and follows the give-up rule."* Also closes the desktop gap (currently one score) so both apps show the same three.

## Problem

The assignment requires **three separate scores — memory, performance, readiness — each with a range and a give-up rule**, on **both** apps, computed in the **shared Rust engine** (re-implementing per platform is a hard-capped grading violation).

Today none of that is true:

- **Desktop** shows exactly **one** score — "GMAT Memory Readiness" `/100` with a range + coverage map + give-up rule ([`anki/qt/aqt/gmat_dashboard.py`](../../../anki/qt/aqt/gmat_dashboard.py) over [`anki/pylib/anki/gmat_readiness.py`](../../../anki/pylib/anki/gmat_readiness.py)).
- **Performance** score does not exist anywhere (only the Jul-2 adaptive design spec).
- **Readiness logic lives in Python** — the phone runs only the Rust engine, so it cannot reuse it. The C-ABI bridge exports **zero** score functions ([`bridge/anki-bridge-rs/src/lib.rs`](../../../bridge/anki-bridge-rs/src/lib.rs)), and the SwiftUI app has no score view ([`ios/AnkiBridgeStub/AnkiBridgeStub/ContentView.swift`](../../../ios/AnkiBridgeStub/AnkiBridgeStub/ContentView.swift)).

So "three scores on the phone" is not a UI gap — the scores don't exist as shared-engine code. This spec builds that shared path.

## Goal

One Rust scoring function is the single source of truth for all three scores; it is surfaced identically to the desktop (Python/Qt) and the phone (Swift/SwiftUI) so both render the same numbers, ranges, and give-up states.

## The three scores (must be genuinely distinct)

The rubric weights "score accuracy and honest uncertainty" at 20% and explicitly warns that blending the three is a fail-risk. Each is a different measurement from different data.

### 1. Memory — "can they recall the fact right now?" (0–100 + range)
- **Source:** FSRS `memory_state` (stability) + card decay → recall probability evaluated at *now*, averaged over studied exam cards. Ports the math in `gmat_readiness._recall_probability` / `_card_recall_band` into Rust.
- **Range:** spread of the per-card recall bands.
- **Distinct because:** pure retention — ignores item difficulty and ignores transfer to new questions.

### 2. Performance — "can they answer a *new* exam-style question that uses this fact?" (0–100 + range)
- **Source:** Rasch / 1PL ability **θ** estimated from the revlog (a review counts as *correct* when ease ≥ 2, i.e. not "Again") joined with each item's difficulty: `aidiff::NN` when the AI calibration has run, else the coarse `difficulty::` tag mapped `easy=20 / medium=50 / hard=80`. `P(correct) = σ(θ − b)`; θ is mapped to a 0–100 display value.
- **Range:** standard error from the Fisher information of the answered items → few answers produce a wide range.
- **Distinct because:** it scores *correctness against difficulty* (transfer to unseen items), not retention. This is the memory→performance bridge the rubric grades, and challenge **7d (paraphrase test)** validates it.

### 3. Readiness — "what would they score today, and how sure are we?" (GMAT **205–805** + range + confidence)
- **Source:** performance θ mapped onto the real GMAT Focus total scale (205–805, steps of 10), **discounted and confidence-gated by topic coverage** (distinct deck topics vs. the GMAT Focus outline — reuses `gmat_readiness._all_outline_topics` / coverage logic). Ability answers "how well on what you've studied"; readiness answers "how well on the *whole* exam given you've only covered X%."
- **Range:** in GMAT points; widens as coverage and answer count fall.
- **Confidence:** an explicit low/medium/high tied to coverage %, mirroring the assignment's own example ("Projected 508, range 503–512, confidence low because 42% coverage").
- **Distinct because:** real exam scale + coverage discount + confidence — not a rescaled ability number.

### Give-up rule (honest, per-score)
Each score abstains independently until it has enough of *its own* data, and reports what is still missing (the rubric asks each score to carry its own give-up rule + the missing data). Concrete thresholds (stated explicitly, per the rubric's "set a clear line and state it"):
- **Memory** → ≥ **30** graded reviews (`MIN_MEMORY_REVIEWS`).
- **Performance** → ≥ **20** graded answers (`MIN_PERF_ANSWERS`) **with at least one right and one wrong** (a Rasch estimate off all-correct or all-wrong is degenerate).
- **Readiness** → strictest, keeps the documented rule from `gmat_readiness`: coverage ≥ **50%** **and** ≥ **200** graded reviews.

Result: after a review session the phone can show memory + performance **with ranges** while readiness **honestly abstains** on coverage — demonstrating both required behaviors ("three scores with ranges" *and* "follows the give-up rule") on one screen.

## Architecture

```
rslib/src/scheduler/gmat_scores.rs   ← NEW: compute_scores(col, deck) → 3 scores + ranges + give-up
        │  read-only: no card.due writes, no transaction → undo history untouched
        ▼
proto GetGmatScores RPC  → GmatScores { memory, performance, readiness : ScoreValue }
        ├────────────► Python (desktop): rewrite gmat_dashboard.py to render 3 scores
        └──► bridge anki_get_scores()  (JSON blob, same pattern as anki_next_card)
                    └──► Swift AnkiEngine.scores()  →  SwiftUI three-score panel
```

## Components & interfaces

| Component | Language | Responsibility | Interface |
| --- | --- | --- | --- |
| `gmat_scores` module | Rust | compute all three scores + ranges + give-up from FSRS state, revlog, difficulty tags, coverage | `compute_scores(col, deck) -> GmatScores` (read-only) |
| `GetGmatScores` | proto/Rust | expose scores to all clients | protobuf RPC → Python + (via bridge) Swift; mirrors existing `GetTopicMasteryStats` |
| `anki_get_scores` | Rust (bridge) | C-ABI wrapper returning a small JSON blob | `bridge/anki-bridge-rs/src/lib.rs`, freed via `anki_free_response` |
| `AnkiEngine.scores()` | Swift | decode the JSON blob into a `Scores` struct | same pattern as `nextCard()` |
| phone score panel | SwiftUI | three Bauhaus score blocks: number+range, or abstain + what's-missing | reached from the header; also shown on session-complete |
| desktop dashboard | Python/Qt | render three scores from the RPC (replaces single readiness) | rewrite `gmat_dashboard.py` |

### Proto message shape
```proto
message GmatScores {
  ScoreValue memory = 1;       // 0-100
  ScoreValue performance = 2;  // 0-100
  ScoreValue readiness = 3;    // GMAT 205-805
}
message ScoreValue {
  bool abstained = 1;
  double score = 2;            // valid only when !abstained
  double low = 3;
  double high = 4;
  string unit = 5;             // "pct" | "gmat"
  string confidence = 6;       // "low" | "medium" | "high" (readiness; empty otherwise)
  repeated string reasons = 7; // main drivers behind the number
  repeated string missing = 8; // what data is still needed (drives the give-up display)
}
```
The bridge JSON blob is a 1:1 flattening of this message, so Swift and Python read identical fields.

## Data flow

Read-only per score:
- **Memory:** iterate the deck's exam cards → each card's FSRS recall probability at now → mean + band.
- **Performance:** load revlog for the deck's cards → (correct?, difficulty) pairs → Rasch θ (MLE or online gradient) → σ(θ) display + SE range.
- **Readiness:** let `p = σ(θ)` (expected proportion correct on an average-difficulty item). Coverage-discount it toward chance: `p_adj = p * coverage_fraction + 0.25 * (1 − coverage_fraction)` (0.25 ≈ 4-choice guess floor for the unstudied part). Map to the scale and round to the nearest 10: `projected = round₁₀(205 + p_adj * 600)`. The range maps `low/high` of θ the same way, then widens by `(1 − coverage_fraction)`. Confidence: `high` ≥ 80% coverage, `medium` ≥ 50%, else `low`.

No `card.due` writes, no transactions — exactly like the points-at-stake ordering and the mastery query, so undo history stays replayable (asserted by a test).

## Error handling / edge cases
- **No AI difficulty tags yet:** performance falls back to coarse `difficulty::` tags; still works at baseline quality.
- **Cold start / too little data:** the relevant score abstains with a "what's missing" list (give-up rule).
- **All-correct or all-wrong history:** performance abstains (degenerate Rasch estimate) rather than emitting ±∞.
- **Offline:** all inputs are local (FSRS state, revlog, tags); no network at score time.
- **No GMAT deck / empty collection:** all three abstain with a clear message.

## Testing (matches the Jul 1 pattern)

Rust unit tests in `gmat_scores.rs` (`#[cfg(test)]`):
1. `ability_rises_on_correct_falls_on_wrong` — performance θ direction.
2. `performance_range_brackets_estimate` — `low < score < high`.
3. `missing_aidiff_falls_back_to_coarse` — coarse-tag difficulty fallback.
4. `three_scores_are_distinct` — memory, performance, readiness are computed from different inputs and can diverge (feed data where memory is high but performance low).
5. `scores_are_read_only_undo_intact` — no writes; undo history replayable.
6. `abstains_below_data_thresholds` — each score's give-up rule fires with a `missing` list.

Python test (`anki/pylib/tests/test_gmat_scores.py`):
- Drive `col._backend.get_gmat_scores(...)` end-to-end and assert a scored **or** correctly-abstaining result for each of the three — like `test_topic_mastery.py` / `test_gmat_readiness.py`.

## Upstream files touched (merge cost)

New files (zero conflict): `rslib/src/scheduler/gmat_scores.rs`, `anki/pylib/tests/test_gmat_scores.py`.

Modified upstream files (small, additive, mirrors the existing T2/T3 footprint):
- `anki/proto/anki/scheduler.proto` (+1 RPC, +2 messages)
- `anki/rslib/src/scheduler/mod.rs` (+`mod gmat_scores;`)
- `anki/rslib/src/scheduler/service/mod.rs` (RPC delegator, mirrors `get_topic_mastery_stats`)
- `bridge/anki-bridge-rs/src/lib.rs` (+`anki_get_scores`) → rebuild `AnkiRust.xcframework`
- `ios/AnkiBridgeStub/AnkiBridgeStub/AnkiBridge.swift` (+`scores()`), `ContentView.swift` (+score panel)
- `anki/qt/aqt/gmat_dashboard.py` (render three scores)

## Scope (today, Fri Jul 3)

All of the above — the full Friday phone requirement plus desktop parity — designed for full marks. Not in scope here: the AI difficulty *calibration* run and its eval/ablation (separate Friday AI workstream); performance uses the coarse-tag fallback until calibration runs, and picks up `aidiff::` automatically when present.

## Coordination boundary (parallel agent owns the adaptive/AI lane)

A separate agent owns **adaptive selection + AI difficulty calibration** and has the reins on that lane. To avoid colliding in shared `rslib`:

- **This lane stays out of:** `rslib/src/scheduler/adaptive.rs`, `rslib/src/scheduler/queue/builder/*` (the toggle-gated selection hook), the AI calibration script + eval, and the `gmat_adaptive_enabled` toggle config.
- **This lane owns:** `gmat_scores.rs`, `GetGmatScores` proto/RPC, `anki_get_scores` bridge export, and the desktop/phone score UI.
- **Seam #1 (queue builder):** scores are strictly read-only and never touch `queue/builder/*` — confirmed clean.
- **Seam #2 (ability θ):** each lane computes its own θ; the only shared input is the `aidiff::NN` tag (with coarse `difficulty::` fallback here, so this lane is not blocked on calibration). If the other lane later wants a single θ source, `gmat_scores.rs` can expose a `pub fn` for it — deferred to avoid coupling now.
- **Only shared file this lane edits:** a one-line `mod gmat_scores;` in `scheduler/mod.rs` plus additive `scheduler.proto` / service-delegator entries — distinct regions, done on branch `three-scores-shared-engine`.

## Open questions / risks
1. **Rasch stability on a small deck (47 items):** with few answers θ is noisy — mitigated by the wide SE range and the performance give-up threshold. Honest by construction.
2. **Readiness↔performance distinctness:** readiness must visibly differ from a rescaled performance — coverage discount + confidence provide that; the `three_scores_are_distinct` test guards it.
3. **xcframework rebuild:** the bridge change requires rebuilding the xcframework for the phone to see `anki_get_scores` (same step as the prior sync-wrapper rebuild).
