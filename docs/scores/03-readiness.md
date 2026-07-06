# Readiness (projected GMAT 205–805)

## What it answers

*What would the student score on the real exam today, and how sure are we?*

Readiness is the [Performance](02-performance.md) ability estimate projected
onto the official **GMAT Focus 205–805** scale, then discounted for the parts of
the exam the student hasn't studied yet. It is the strictest of the three
scores — it refuses to show a number unless there's both enough answering
history *and* enough topic coverage to make a projection defensible.

## How it's computed

**Model: scale projection of the Rasch θ, discounted by coverage.** There is no
new estimator here — Readiness reuses the same θ and SE from
[Performance](02-performance.md) and maps them onto 205–805.

- **Code:** `anki/rslib/src/scheduler/gmat_scores.rs:readiness_score`
  (lines 299–357), taking the `PerfEstimate` computed for Performance.
- **Coverage** (`coverage_fraction`, lines 361–373) = the fraction of the
  28-entry GMAT Focus outline (`OUTLINE_TOPICS`, lines 53–82) that any studied
  card's topic tag covers. A card's topic tag is the first `::`-bearing tag
  whose namespace is `Quant` / `Verbal` / `DataInsights` — an allowlist, so
  auxiliary tags (`id::`, `difficulty::`, `split::`, …) are ignored
  (`card_topic_tag`, lines 381–390; `topic_covers`, lines 394–398).
- **Discount + scale map** (lines 328–333). Expected proportion-correct is
  pulled toward the 4-choice guess floor (`GUESS_FLOOR = 0.25`) by the
  *uncovered* fraction `(1 − c)`, then mapped with `GMAT_MIN = 205` and
  `GMAT_SPAN = 600`:

  ```
  to_gmat(p) = 205 + [ p·c + 0.25·(1 − c) ] · 600
  score      = round_to_10( to_gmat( σ(θ) ) )
  ```

  where `c` = coverage. `round_to_10` snaps to the nearest 10, matching real
  GMAT score reporting (`unit = "gmat"`, range 205–805).

## The confidence range

Two sources of uncertainty compound: the ability estimate's own 95% CI *and*
the exam left uncovered (lines 334–338). The θ interval is projected onto the
scale, then padded by up to ±60 points for missing topics
(`widen = (1 − c) · 60`):

```
low  = round_to_10( max(205, to_gmat(σ(θ − 1.96·se)) − widen) )
high = round_to_10( min(805, to_gmat(σ(θ + 1.96·se)) + widen) )
```

so the band is the Performance 95% CI on the scale, widened by the uncovered
fraction, clamped to `[205, 805]` (`GMAT_MAX_CLAMP = GMAT_MIN + GMAT_SPAN =
805`). Readiness is the one score that also reports a `confidence` label, driven
by coverage (lines 339–345):

| Coverage `c` | Confidence |
|---|---|
| ≥ 0.80 | `high` |
| ≥ 0.50 | `medium` |
| < 0.50 | `low` |

## When it abstains (the give-up rule)

Readiness abstains unless **all three** conditions hold (lines 303–325); each
unmet one adds its own line to the `missing` list the student sees:

1. **Reviews.** Total reviews ≥ `MIN_READINESS_REVIEWS = 200` — else
   *"Answer at least 200 cards (have N)."*
2. **Coverage.** Coverage ≥ `MIN_READINESS_COVERAGE = 0.50` — else
   *"Cover at least 50% of exam topics (currently X%)."*
3. **Performance is estimable.** If [Performance](02-performance.md) itself
   abstained, its `missing` message is passed through, so a deck that can't pin
   an ability can never show a projected score.

## Why this is honest

Readiness never dresses up a thin deck as "ready." Projecting from a real IRT
ability rather than raw percent-correct, discounting toward a guess floor for
unstudied material, and widening the band for missing coverage all push the
number *down and wider* exactly when the evidence is weaker. And it is candid
about its own limit: a fully validated score model needs students who studied
*and* took real practice tests over time, which isn't gatherable in a week — so
we prove the **steps of the bridge** (calibrated memory → performance on
held-out questions → a documented scale mapping with a range) and state plainly
that the projected number is not yet validated against real practice-test
outcomes ([`../MODELS.md`](../MODELS.md) §3).

> **Note on the Python module.** `anki/pylib/anki/gmat_readiness.py` predates
> the Rust engine and computes a *different* readiness number — mean FSRS recall
> mapped to 0–100 with coarse difficulty-driven bands, and a `graded_reviews`
> (revlog) count for its 200-review floor. The authoritative, cross-platform
> Readiness score shipped to both apps is the Rust `readiness_score` described
> above (`GetGmatScores`). This page documents the Rust engine.

---

**See also:** [Memory (FSRS recall)](01-memory.md) ·
[Performance (Rasch / 1PL ability)](02-performance.md) ·
[full methodology index](README.md)
