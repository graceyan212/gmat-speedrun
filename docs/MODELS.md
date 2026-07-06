# Model descriptions — Memory, Performance, Readiness

Three **separate** models answer three different questions. All three are computed
in Rust (`anki/rslib/src/scheduler/gmat_scores.rs`, the `GetGmatScores` RPC), so
the desktop app and the phone show identical numbers. Each returns a point
estimate **and a range**, the % of the exam covered, a "last updated" time, the
main reasons behind it, and a **give-up rule** — below which it shows *nothing*
rather than a number it can't defend. Thresholds live at the top of
`gmat_scores.rs`; the abstain path leaves `score`/`low`/`high` unset (proto:
"valid only when `!abstained`").

---

## 1. Memory — "can the student recall this fact right now?"

- **What / how.** Anki's built-in **FSRS** retrievability for each card; the
  Memory score is the deck-level recall it implies. FSRS is a well-validated
  spaced-repetition memory model — we do **not** re-invent it; we report it
  honestly and, crucially, keep it *separate* from Performance and Readiness.
- **Unit / range.** Percent recall, with a range.
- **Calibration.** When it says 80%, the student should recall ~80% of the time.
  Proven on **held-out** reviews with a reliability diagram + Brier score in
  [`RESULTS.md`](RESULTS.md) §3.
- **Give-up rule.** Abstains until **≥ 30 graded reviews** — below that there
  isn't enough evidence to state a recall rate.

## 2. Performance — "can the student answer a *new*, exam-style question that uses this fact?"

- **What / how.** A **Rasch / 1PL** ability estimate (real item-response theory):
  a Newton solver estimates the student's latent ability `θ` from their
  correct/incorrect answers on items of known difficulty `b`
  (`P(correct) = σ(θ − b)`). Difficulty `b` comes from the AI-difficulty rating
  (or the coarse `difficulty::` tag as a fallback) — **not** from the student's
  own reviews, so the estimator can't leak its own labels.
- **Why separate from Memory.** Remembering a card ≠ answering a reworded
  question. The [paraphrase test](PARAPHRASE_TEST.md) measures the gap directly:
  recall on the memorized card (86.8%) vs accuracy on reworded questions (71.3%)
  — a **15.5-point** gap, so Performance is not an echo of Memory.
- **Unit / range.** Percent, with a range (SE of the ability estimate).
- **Give-up rule.** Abstains until **≥ 20 answered questions with a mix of right
  and wrong** — an all-right or all-wrong record can't pin `θ`.

## 3. Readiness — "what score would the student get today, and how sure are we?"

- **What / how.** The Performance ability estimate projected onto the real
  **GMAT Focus 205–805** scale, reported as a point estimate **plus a likely
  range plus a confidence level** (low / medium / high). Method + mapping in
  [`SCORE_MAPPING.md`](SCORE_MAPPING.md).
- **Unit / range.** GMAT 205–805, e.g. *"545, likely 505–585, confidence: low —
  you've studied 42% of the exam."*
- **Give-up rule (the strictest).** Abstains unless **≥ 200 graded reviews AND
  ≥ 50% topic coverage AND** Performance is itself non-abstaining. A deck that
  skips a high-weight section cannot show "ready." Coverage is measured against
  the 28-topic outline in [`taxonomy.md`](../content/taxonomy.md); when it
  abstains, the dashboard lists exactly what's missing.
- **Honesty note.** A true score model needs students who studied *and* took real
  practice tests, tracked over time — not gatherable in a week. So we prove the
  **steps of the bridge** (calibrated memory → performance on held-out questions →
  a documented mapping with a range) and are explicit that the projected number
  is not yet validated against real practice-test outcomes. "We calibrated memory
  but can't yet prove the projected score" is stated plainly rather than dressed
  up as a measurement.

---

**Reproduce the numbers:** `python3 content/tools/eval_difficulty.py` (difficulty
eval + leakage), see [`RESULTS.md`](RESULTS.md); calibration reliability diagram in
the same run. The give-up thresholds are asserted by Rust tests in
`gmat_scores.rs` (`readiness_abstains_below_coverage_and_reviews`).
