# Performance (Rasch / 1PL ability)

## What it answers

*Can the student answer a new, exam-style question — not just recall a card
they've already memorized?*

Performance is a latent-ability estimate from item-response theory. It weighs
every answer by how hard the item was, so getting hard questions right counts
for more than getting easy ones right. This is deliberately **not** an echo of
[Memory](01-memory.md): the paraphrase test shows a ~15.5-point gap between
recall on the memorized card and accuracy on reworded questions
([`../PARAPHRASE_TEST.md`](../PARAPHRASE_TEST.md)).

## How it's computed

**Model: Rasch / 1PL (one-parameter logistic) item-response theory.** Each item
has a difficulty `b`; the student has a latent ability `θ`; the probability of a
correct answer is `P(correct) = σ(θ − b)` where `σ(x) = 1/(1+e^(−x))`.

- **Code:** `anki/rslib/src/scheduler/gmat_scores.rs`, struct `PerfEstimate`
  (`from_rows`, lines 171–227; `to_score_value`, lines 239–258). Same read-only
  `TopicCardRow` rows as Memory.
- **Inputs.** Every answered card (`total > 0`) contributes a triple
  `(b, c, n)`: item difficulty logit `b`, `c` correct answers, `n` total
  attempts (from the revlog pass/total tallies).
- **Item difficulty `b`.** Comes from tags, *not* from the student's own
  reviews (so the estimator can't leak its own labels) — `difficulty_logit` /
  `difficulty_0_100` (lines 262–293):
  - `aidiff::NN` (AI-rated 0–100) wins when present and **finite**; non-finite
    values like `aidiff::nan`/`aidiff::inf` are rejected and fall through, since
    a NaN would poison θ.
  - else the coarse `difficulty::easy|medium|hard` tag → **20 / 50 / 80**.
  - else neutral **50**.

  The 0–100 value maps to a logit with `DIFFICULTY_SCALE = 4.0` (a ±2-logit
  range):

  ```
  b = (difficulty/100 − 0.5) · 4.0     (0→−2, 50→0, 100→+2)
  ```

- **θ via Newton–Raphson MLE** (lines 193–211). Starting from `θ = 0`, up to 50
  iterations, with `p_i = σ(θ − b_i)`:

  ```
  grad = Σ_i (c_i − n_i · p_i)          (score function)
  info = Σ_i n_i · p_i · (1 − p_i)      (Fisher information)
  θ   ← θ + grad / info
  ```

  It stops when `|step| < 1e-6` (or `info < 1e-9`), then clamps `θ` to
  `[−6, 6]`.

## The confidence range

The band is a genuine **95% confidence interval** on the ability estimate,
derived from the Fisher information at the final θ (lines 212–245):

```
info = Σ_i n_i · p_i · (1 − p_i)
se   = 1 / sqrt(info)                  (= 3.0 if info ≈ 0)

mid  = σ(θ)            · 100
low  = σ(θ − 1.96·se)  · 100
high = σ(θ + 1.96·se)  · 100
```

More answers (higher information) → smaller `se` → a tighter band. Because the
bounds are pushed through the sigmoid, the interval is asymmetric in percent
space but always inside (0, 100) by construction. Like Memory, Performance
reports no `confidence` label; the ±1.96·SE band carries the uncertainty
(`unit = "pct"`).

## When it abstains (the give-up rule)

Performance abstains (`unavailable(...)`, which becomes `abstained = true` with
a `missing` list) in two cases (lines 181–191):

1. **Too few answers.** If total attempts `< MIN_PERF_ANSWERS = 20`
   (`gmat_scores.rs:37`): *"Answer at least 20 questions (with a mix of right
   and wrong) to estimate performance (have N)."*
2. **All right or all wrong.** If `n_correct == 0 || n_correct == n_answers`,
   θ is not estimable at the extremes (the MLE runs off to ±∞), so it returns:
   *"Need both correct and incorrect answers to estimate ability."*

## Why this is honest

This is real item-response theory, not a raw percent-correct dressed up: an
identical raw score yields a different θ depending on whether the items were
easy or hard. Difficulty comes from item tags rather than the student's own
performance, so the estimator cannot leak its own labels into its inputs (the
leakage scan is clean — [`../RESULTS.md`](../RESULTS.md) §2.1.1). The band is a
statistically-grounded 95% CI from Fisher information, and the all-right /
all-wrong abstain rule refuses to fabricate an ability from a record that can't
pin one down.

---

**See also:** [Memory (FSRS recall)](01-memory.md) ·
[Readiness (projected GMAT 205–805)](03-readiness.md) ·
[full methodology index](README.md)
