# Adaptive GMAT quiz + performance score (AI-calibrated difficulty)

**Date:** 2026-07-02
**Status:** Design approved, pending spec review
**Assignment track:** Fri Jul 3 — "AI added + eval." Adds the missing third score (performance)
and the GMAT-style adaptive test.

## Goal

Add a **computer-adaptive quiz** to the GMAT study app: after each answer the
student's estimated ability updates and the next question is chosen near their
level (harder if right, easier if wrong), like the real GMAT Focus CAT. The
running ability estimate becomes the app's **third score — "performance"** —
shown with a range, alongside the existing memory and readiness scores.

The **AI feature** is difficulty calibration: an LLM rates each question's
difficulty on a fine-grained scale with a cited rationale, replacing the coarse
`difficulty::easy|medium|hard` tags. Those coarse tags become the **baseline the
AI must beat**.

## Why this fits the assignment

| Requirement | How this satisfies it |
| --- | --- |
| AI feature with **traceable sources** | LLM difficulty rating carries a one-line rationale grounded in the question text + taxonomy; saved to `content/items.json` so every rating is traceable, not a black box. |
| **Beats a simple baseline** | Baseline = the existing coarse `difficulty::` tags (6 easy / 29 medium / 12 hard — 62% lumped in "medium"). Eval measures whether AI difficulty predicts the student's actual right/wrong answers better (Brier + accuracy). |
| **Can be switched off** | A collection config flag disables adaptive selection (falls back to normal ordering). Enables the ablation. |
| **Third score (performance)** | The ability estimate is the missing third score (memory ✅, readiness ✅, performance ❌→✅), shown with a range. |
| **Ablation** | adaptive + AI-difficulty  vs.  adaptive + coarse-tags  vs.  plain Anki. |
| **Shared one engine / ships to phone** | Selection + scoring live in `rslib` (Rust) and are exposed over protobuf, so desktop and phone both run the identical logic. |
| **Offline** | AI calibration is a precompute step; difficulties are stored, so review needs no network. |

## Architecture

Two parts, each where it belongs.

### Part 1 — AI difficulty calibration (Python, offline, run once/occasionally)

A script (`content/tools/calibrate_difficulty.py`, or under `pylib/tools/`)
that:

1. Reads `content/items.json` (47 items; fields include `stem`, `choices`,
   `answer`, `topic`, `difficulty`).
2. For each item, calls the Claude API to return a **difficulty 0–100** plus a
   **one-line rationale** grounded in the stem/choices and the topic. Batched;
   cheap model is fine (model choice confirmed at implementation via the
   `claude-api` skill).
3. Writes `ai_difficulty` (int 0–100) and `ai_difficulty_reason` (str) back into
   `content/items.json` — the traceable record.
4. Writes the difficulty onto each note in the collection so the Rust engine can
   read it (see Data model).

This never runs during study, so review stays fully offline.

#### Difficulty rubric (how the score is decided)

The score is **not** a free-form LLM guess. The model rates each item against an
explicit rubric and returns which factors drove the score (the rationale = the
traceable record). Factors:

- **Reasoning steps** — number of distinct steps from stem to answer.
- **Concept load** — single concept vs. combining several.
- **Distractor trickiness** — are the wrong choices engineered to catch common errors?
- **Parsing/wording** — convoluted stems, negations, "all of the following EXCEPT".
- **Computation weight** — quick mental math vs. heavy arithmetic.
- **Intended time** — the item's existing `target_seconds` as a difficulty signal.

Scale: **0–100, anchored to described bands** (e.g. 0–20 trivial recall … 80–100
multi-step with strong traps) so ratings are comparable, not arbitrary;
single-point differences are not treated as meaningful. Output per item:
`{ai_difficulty: int, ai_difficulty_reason: str}` — e.g. *"78 — 3-step
ratios+percents chain; choice (C) is an off-by-one trap."*

**This is a proxy, not ground truth.** True difficulty is the empirical p-value
(fraction of real test-takers who miss it); the LLM judges *human* difficulty
from the rubric, since it is not itself a test-taker. The eval (below) validates
the proxy against the student's actual answers and reports honestly if it does
not beat the coarse baseline.

### Part 2 — Adaptive selection + performance score (Rust, `rslib`, shared)

A new module (`rslib/src/scheduler/adaptive.rs`) that:

- Estimates the student's **ability θ** from their answer history joined with
  item difficulty, using the **Rasch / 1PL** model: `P(correct) = σ(θ − b)`,
  where `b` = item difficulty (rescaled from 0–100 to logits) and `θ` = ability.
- Exposes the **performance score** = θ mapped to a 0–100 display value, with a
  **range** from the estimate's standard error.
- Provides **adaptive selection**: extends the Jul 1 points-at-stake reorder as a
  hierarchy — topic weakness sets the priority (as Wed already does), and *within*
  that, the card nearest the current θ is chosen.

Exposed to all clients via a new protobuf RPC `GetPerformanceScore`. Adaptive
selection hooks into the existing queue-build path (behind the toggle) so the
phone's existing review loop serves adaptive-ordered cards with no phone code
change — the same mechanism by which the Jul 1 points-at-stake ordering already
reaches the phone.

## Data flow

```
items.json ──calibrate (Claude)──► items.json + notes get ai_difficulty
                                            │
                          (offline, cached) │  stored on each note
                                            ▼
  study session ──► queue build (toggle ON) reads difficulty per card
                          │
                          ├─ compute θ from revlog history + difficulty (Rasch)
                          ├─ order next card: weakness (primary) then difficulty-fit (secondary)
                          └─ GetPerformanceScore RPC ► desktop/phone shows score+range
```

## Data model / storage

- **Source of truth:** `content/items.json` gains `ai_difficulty` (0–100) and
  `ai_difficulty_reason` per item.
- **In the collection (read by Rust):** each note gets its difficulty as a tag
  `aidiff::NN` (NN = 0–100). Rationale: the engine already parses tags per card
  (`topic_mastery` / `topic_stats` read the space-separated tags string), so a
  tag is the lowest-friction way to expose a per-card number to Rust without a
  schema/note-type change. (Alternative considered: a note field or a side
  table; deferred as a heavier option.)
- The coarse `difficulty::easy|medium|hard` tags are left in place — they are the
  baseline and the ablation's alternate difficulty source.

## Components & interfaces

| Component | Language | Responsibility | Interface |
| --- | --- | --- | --- |
| `calibrate_difficulty` | Python | LLM difficulty + rationale → items.json + `aidiff::` tags | CLI script |
| `adaptive` module | Rust | Rasch θ estimate, SE→range, nearest-difficulty selection | internal + RPC |
| `GetPerformanceScore` | proto/Rust | return `{score, score_low, score_high, answered, abstained}` | protobuf RPC → Python/Swift bindings |
| queue-build hook | Rust | extends points-at-stake: weakness (primary) + difficulty-fit vs θ (secondary); toggle gates the difficulty term | internal, in `queue/builder` |
| desktop score UI | Python/Qt | show performance score + range | reuses readiness-dashboard style |
| eval harness | Python | Brier + accuracy: AI difficulty vs coarse tags on the student's own answers | CLI script + test |
| toggle | Rust config | `gmat_adaptive_enabled` bool | collection config |

## Performance-score details (Rasch / 1PL)

- Difficulty `b` = `(ai_difficulty/100 − 0.5) * SCALE` (map 0–100 to a logit
  range; SCALE ~4 gives roughly ±2 logits).
- Ability `θ` estimated by maximizing the Rasch likelihood over the student's
  answered items (or a simple online gradient update per answer). "Correct" =
  revlog ease ≥ 2 (i.e., not "Again").
- **Range** = θ ± standard error (from the Fisher information of the answered
  items); mapped back to the 0–100 display scale. Few answers ⇒ wide range.
- **Give-up rule (reused philosophy):** below a minimum number of graded answers,
  `GetPerformanceScore` abstains (no number, lists what's needed) — mirrors the
  existing readiness give-up rule so all three scores behave consistently.

## Adaptive selection (hierarchy merge with Wed's ordering)

There is exactly one "next card" decision, and one ordering fills it — so this
**extends** the Jul 1 points-at-stake reorder (`sort_review` in the queue
builder) instead of competing with it. The ordering is a **hierarchy**:

1. **Primary — topic weakness (Wed):** weak-topic cards are prioritized, exactly
   as points-at-stake already does.
2. **Secondary — difficulty fit (new):** *within* that priority, choose the card
   whose difficulty is nearest the student's current ability θ.

Concretely, the existing per-note weight becomes the coarse sort key and
`-|difficulty − θ|` becomes the tiebreaker (one weight, one stable sort, same
hook) → weakness sets the neighborhood, difficulty picks the card, and it reaches
the phone for free via the existing review path.

- **Friday:** the merged hierarchy ordering above (re-derived per session as θ
  updates from answer history). Right→harder is present, softened by the weakness
  pull — an accepted trade-off for reusing the Wed hook with minimal work.
- **Stretch (Sunday):** a standalone "Adaptive Quiz" mode governed *purely* by
  difficulty-vs-θ (crisp per-answer right→harder) with true per-answer re-pick.
- Cards with no `aidiff::` tag fall back to the coarse `difficulty::` value
  (easy=20, medium=50, hard=80) so selection degrades gracefully.

The **toggle** gates the secondary term: off ⇒ pure Wed weakness ordering (the
ablation's "adaptive off" arm); on-with-AI-difficulty vs on-with-coarse-tags are
the other two ablation arms.

## Eval (uses the student's own answers)

- Log the student's adaptive-session answers (already in the revlog: card +
  correct/incorrect).
- Held-out split (e.g., estimate θ on the first 70% of answers, score
  predictions on the last 30%).
- For each held-out answer, predict `P(correct)` two ways: using **AI
  difficulty** and using **coarse-tag difficulty**.
- Compare **Brier score** (lower = better calibrated) and **accuracy**. Success
  = AI difficulty's Brier is meaningfully lower than the coarse baseline's.
- **Ablation table:** adaptive + AI-difficulty / adaptive + coarse-tags / plain
  Anki — reported for the Sunday write-up.

## Error handling / edge cases

- **Calibration not yet run:** no `aidiff::` tags → selection + score fall back to
  coarse tags; feature still works, just at baseline quality.
- **Cold start (no answers):** performance score abstains with a "not enough
  data yet" message (give-up rule).
- **Offline:** all difficulty is precomputed; no network at review time.
- **Undo / no corruption:** selection and score are **read-only** — no
  `card.due` writes, no transactions — exactly like points-at-stake ordering and
  the mastery query, so the undo history is untouched (asserted by a test).

## Testing (matches the Jul 1 pattern)

Rust unit tests (`adaptive.rs`, `#[cfg(test)]`):
1. `ability_rises_on_correct_falls_on_wrong` — Rasch update direction.
2. `next_card_picks_nearest_difficulty` — selection chooses the closest-to-θ card.
3. `missing_aidiff_falls_back_to_coarse` — graceful fallback.
4. `adaptive_selection_and_score_leave_undo_intact` — no writes; undo replayable.
5. `performance_score_reports_a_range` — score_low < score < score_high.

Python test (`pylib/tests/test_performance_score.py`):
- Drive `col._backend.get_performance_score(...)` end-to-end and assert a scored
  (or correctly-abstaining) result — like `test_topic_mastery.py`.

## Upstream files touched (merge-difficulty)

New files (zero conflict): `rslib/src/scheduler/adaptive.rs`,
`pylib/tests/test_performance_score.py`, `content/tools/calibrate_difficulty.py`,
eval script, desktop score-UI additions.

Modified upstream files (small, additive, mirrors the T2 footprint):
`proto/anki/scheduler.proto` (+1 RPC/messages), `rslib/src/scheduler/mod.rs`
(+`mod adaptive;`), `rslib/src/scheduler/service/mod.rs` (RPC delegator),
`rslib/src/scheduler/queue/builder/*` (selection hook behind the toggle).

## Scope split for the deadline

- **Friday (Jul 3):** calibration script + `aidiff::` tags; Rust performance
  score (θ + range); the **hierarchy-merge ordering** (weakness → difficulty-fit)
  extending the Wed points-at-stake hook, behind the toggle; desktop shows the
  score; first eval on the student's own answers; Rust + Python tests.
  (Engine is shared, so the phone gets the merged ordering underneath.)
- **Sunday (Jul 5):** standalone "Adaptive Quiz" mode (pure difficulty-vs-θ) with
  true per-answer re-selection; phone score readout (SwiftUI); fuller eval
  (calibration curve / Brier) + the ablation table + write-up.

## Open questions / risks

1. **Margin over baseline:** because the questions were AI-authored, the coarse
   buckets may already be decent, so AI difficulty might not beat them by a large
   Brier margin. The granularity gain (62% un-differentiated "medium" → a
   continuous scale) is real regardless, but the "beats baseline" claim depends
   on the eval landing.
2. **Enough answer data:** the eval needs the student to actually answer enough
   questions (~30–50, mix of right/wrong) for a meaningful held-out comparison.
3. **MVP adaptivity vs true CAT:** Friday ships session-level difficulty ordering;
   true per-answer re-pick is the Sunday stretch.
