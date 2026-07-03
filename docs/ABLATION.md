# Ablation: does the adaptive study feature help?

**This is a SIMULATION, not a human study.** A real evaluation would run three
parallel builds of the app against real learners for an *equal amount of study
time* and compare score gains. That was not feasible solo, so this is a
Monte-Carlo proxy: a simulated learner with a fixed *true* ability answers items
under three item-selection policies, holding the **study budget equal** (same
number of item-attempts `N` per arm, same random seeds), and we measure how well
each policy targets item difficulty and recovers the learner's ability.

Reproduce:

```
cd /Users/graceyan/Desktop/alpha/speedrun && python3 content/tools/ablation.py
```

The run is deterministic (fixed seeds), so the numbers below reproduce exactly.

## What is being compared

Three arms, all drawing items from the same deck and answering under the same
Rasch model, differing only in **which item they pick next**:

| Arm | Policy | Mirrors |
|-----|--------|---------|
| **ADAPTIVE** | Pick the item whose difficulty `b` is nearest the learner's *current* estimated ability `theta_hat`, re-estimating `theta_hat` after every answer. | `adaptive.rs` — the `fit_distance = |b - theta|` tie-break, plus `estimate_ability` (Rasch/1PL Newton solver). |
| **OFF** | The app's default *non-adaptive* order: points-at-stake, weakest-topic first (`topic_weight x weakness`, highest weight first). `theta` is still estimated for the metric but does **not** steer selection. | `sorting.rs::sort_review` / `topic_mastery::points_at_stake_weights`. |
| **PLAIN** | Random / DB order, no weighting at all — a plain-Anki proxy. | Default Anki review gather order. |

## Method

- **Model (Rasch / 1PL).** Each item has difficulty `b` (logit). A learner with
  true ability `true_theta` answers correctly with `P = sigmoid(true_theta - b)`.
  The Rasch math in `ablation.py` (`sigmoid`, `difficulty_to_logit`,
  `estimate_theta`) is a direct port of `anki/rslib/src/scheduler/adaptive.rs`,
  including `SCALE = 4.0`, the `[-4, 4]` theta clamp, and the 10-iteration
  Newton solver — so the simulation estimates ability with the *same* estimator
  the shipped app uses.
- **Item difficulties** are drawn from the real deck distribution in
  `content/items.json` + `content/ai_difficulty.json`, read exactly the way
  `adaptive.rs::note_difficulty` reads it: prefer the AI rating, else the coarse
  `difficulty::easy|medium|hard` tag mapped to 20 / 50 / 80. This yields
  **369 items**, difficulty mean **52.2** / sd **21.0** on the 0-100 scale
  (range 16-82), which map to logits `b` in **[-1.36, +1.28]** via
  `b = (d/100 - 0.5) * 4.0`. (68 items are finely AI-rated; the rest carry
  coarse tags, which is why the distribution clusters at 20/50/80.)
- **Equal budget.** Every arm runs `N = 60` item-attempts. Repeated items
  aggregate `passed/total` per item, mirroring how the app counts answers from
  the revlog.
- **Seeds.** `200` seeds. Each seed draws one learner's `true_theta` uniformly
  in `[-2, +2]` logits, and that *same learner* is run through all three arms
  (paired comparison), so results are not specific to one ability level.

### Metrics

- **(a) Desirable-difficulty band** — fraction of presented items with
  `|b - true_theta| < 1.0` logits (items neither too easy nor too hard for the
  learner). Higher is better; ADAPTIVE should be highest.
- **(b) Ability-estimation error** — `|theta_hat - true_theta|` after all `N`
  items. Lower is better; ADAPTIVE should converge fastest.

## Results (ACTUAL numbers from the run)

`N = 60` attempts/arm, `200` seeds, band = `|b - true_theta| < 1.0`, mean +/- sd:

| Arm | % items in desirable band (higher better) | `|theta_hat - true_theta|` @ N (lower better) |
|-----|:---:|:---:|
| **ADAPTIVE** | **95.0% +/- 8.3%** | **0.212 +/- 0.161** |
| OFF | 47.5% +/- 24.1% | 0.271 +/- 0.189 |
| PLAIN | 46.1% +/- 22.8% | 0.292 +/- 0.251 |

**Summary.**
- **Targeting:** ADAPTIVE keeps **95.0%** of items in the desirable band vs
  **47.5%** (OFF) and **46.1%** (PLAIN) — about **+48.9 percentage points** more
  well-targeted items than plain-Anki order, for the same study budget.
- **Ability recovery:** ADAPTIVE's ability-estimation error is **0.212** logits
  vs **0.292** for PLAIN — roughly **28% lower** — because deliberately probing
  items near `theta_hat` is exactly where a Rasch item is most informative
  (Fisher information peaks at `b = theta`).

The predicted ordering holds on both metrics: ADAPTIVE best, OFF and PLAIN close
behind each other.

## Honest caveats

- **This is a simulation, not evidence from users.** It shows the *mechanism*
  works as designed under the model's own assumptions; it does **not** prove a
  learning gain for real students. The intended real test is a three-build,
  equal-study-time human A/B/C comparison, which was out of scope solo.
- **The model is the thing being assumed, then measured.** Answers are generated
  by the same Rasch/1PL model the estimator inverts. Real GMAT items violate 1PL
  (varying discrimination and guessing, i.e. 2PL/3PL effects; learning during
  study; careless slips), none of which are modeled here. The in-band metric is
  especially favorable to ADAPTIVE almost by construction — selecting `b` near
  `theta_hat` *is* selecting into the band once `theta_hat` is near `true_theta`.
- **Difficulty data is coarse.** Only 68 of 369 items are finely AI-rated; the
  rest sit at exactly 20/50/80, so the effective difficulty grid is lumpy and
  the true deck spread on the ability axis is narrow (`b` in ~[-1.4, +1.3]). A
  richer, fully AI-rated deck would change absolute band percentages.
- **OFF is a faithful-but-partial proxy.** It reproduces the points-at-stake
  weakest-topic ordering, but "weakness" here is a running per-topic miss rate,
  not the full `topic_mastery` query; topic labels are the item topics from the
  deck. The real feature also composes with due-date ordering, which is not
  modeled.
- **`theta_hat` is not used to *grade*, only to *select* / *report*.** The band
  metric judges against `true_theta` (known only in simulation); in the app the
  learner never sees `true_theta`, so the app-side benefit is the reported score
  range converging, which metric (b) stands in for.
- **Absolute numbers are seed- and parameter-dependent** (`N`, seed count,
  `true_theta` range, `SCALE`, band width). The *ordering* of the arms is the
  robust finding; the exact percentages are not a promise about production.
