# Memory (FSRS recall)

## What it answers

*Can the student remember what they've already studied, right now?*

Memory is a snapshot of retention across the cards the student has actually
reviewed. It does **not** ask whether they can solve a fresh exam question
(that's [Performance](02-performance.md)) — only whether the material they've
put into the deck is currently retrievable.

## How it's computed

**Model: FSRS retrievability (the forgetting curve).** We do not re-invent a
memory model; we read Anki's built-in FSRS state per card and report the
deck-level recall it implies.

- **Code:** `anki/rslib/src/scheduler/gmat_scores.rs:memory_score`
  (lines 112–156), fed by the read-only storage query
  `all_topic_card_rows` (one `TopicCardRow` per review-eligible card, carrying
  FSRS `stability`/`decay`, revlog pass/total tallies, `last_review_ms`, and
  `tags`). No transaction, no card mutation — opening the score view never
  costs the student their undo history.
- **Inputs per card:** `stability` (FSRS memory state), `decay` (the card's own
  decay, else the FSRS-5 default `FSRS5_DEFAULT_DECAY = 0.5`), and
  `last_review_ms`. Cards with no stability, `stability <= 0`, or no
  last-review time are skipped.
- **Estimator.** For each surviving card, elapsed days since the last review is
  `days = max(0, (now − last_review_ms) / 86_400_000)`
  (`MS_PER_DAY`), and recall is the FSRS current retrievability
  (`fsrs::current_retrievability`, called at `gmat_scores.rs:129`):

  ```
  factor = 0.9 ^ (1 / -decay) − 1
  recall = (days / stability · factor + 1) ^ (-decay)
  ```

  At `days == stability` this equals the 0.9 target retention, and it decreases
  as time passes.
- **Aggregate.** The score is the mean of the per-card recalls, as a percentage:
  `score = mean · 100` (`unit = "pct"`, range 0–100).

## The confidence range

The band is the **spread of recall across the student's own cards**, not a
model-fit interval. Taking the mean and standard deviation of the per-card
recalls (`mean_sd`, `gmat_scores.rs:408`):

```
half = sd · 100                       (± 1 SD of per-card recall)
low  = max(0,   score − half)
high = min(100, score + half)
```

So a student whose cards are all similarly well-retained gets a tight band; a
student with some strong and some near-forgotten cards gets a wide one. Memory
reports no `confidence` label (that field is empty for this score) — the ± 1 SD
band carries the uncertainty.

## When it abstains (the give-up rule)

Memory abstains (`abstained = true`, no number, a `missing` list instead of a
score) in two cases (`gmat_scores.rs:113–141`):

1. **Too few reviews.** If the total review count across all rows is
   `< MIN_MEMORY_REVIEWS = 30` (`gmat_scores.rs:36`), it returns:
   *"Answer at least 30 cards to estimate memory (have N)."*
2. **No FSRS state.** If ≥ 30 reviews exist but no card has a usable FSRS memory
   state, it returns: *"No cards with an FSRS memory state yet."*

In both cases the caller (dashboard / phone) shows the `missing` line rather
than a bare percentage.

## Why this is honest

FSRS is a well-validated spaced-repetition model, so the point estimate rests
on real memory science rather than a bespoke heuristic — and its calibration is
checked on held-out reviews (Brier score + reliability diagram in
[`../RESULTS.md`](../RESULTS.md) §3). The band is derived from the student's own
recall spread, not invented, and the 30-review floor stops us from quoting a
recall rate before we've seen the student answer enough cards to mean anything.
Crucially, Memory is kept **separate** from Performance and Readiness: recalling
a memorized card is not the same as answering a reworded exam question, and
conflating the two would overstate readiness.

---

**See also:** [Performance (Rasch / 1PL ability)](02-performance.md) ·
[Readiness (projected GMAT 205–805)](03-readiness.md) ·
[full methodology index](README.md)
