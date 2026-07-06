# The three GMAT scores — full methodology

The GMAT deck reports three deliberately distinct scores, all computed in the
shared Rust engine (`anki/rslib/src/scheduler/gmat_scores.rs`, the
`GetGmatScores` RPC) so desktop and phone show identical numbers. Each answers a
different question, carries its own confidence range, and has an independent
**give-up rule**: below enough of *its own* data it *abstains* — showing a
`missing` list instead of a number it can't defend. These pages are grounded in
the actual implementation (formulas, thresholds, and `file:function`
references); for the condensed tables see [`../MODELS.md`](../MODELS.md) and
[`../SCORE_MAPPING.md`](../SCORE_MAPPING.md).

- [**Memory (FSRS recall)**](01-memory.md) — can the student remember what
  they've studied right now? Mean FSRS retrievability across studied cards.
- [**Performance (Rasch / 1PL ability)**](02-performance.md) — can they answer a
  new, exam-style question? A Newton-solved 1PL ability estimate `θ` weighted by
  item difficulty.
- [**Readiness (projected GMAT 205–805)**](03-readiness.md) — what would they
  score today, and how sure are we? The Performance ability projected onto the
  205–805 scale, discounted by topic coverage.
