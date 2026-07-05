# GMAT Score Mapping

How the three GMAT scores are computed, their ranges, and the give-up
(abstain) thresholds. All logic lives in
`anki/rslib/src/scheduler/gmat_scores.rs`; the Rasch model it shares with
adaptive card selection is documented in
`anki/rslib/src/scheduler/adaptive.rs`.

The three scores are **deliberately distinct measurements** computed from a
single read-only storage query (`all_topic_card_rows`, one `TopicCardRow`
per review-eligible card carrying FSRS stability/decay, revlog pass/total
tallies, last-review time, and tags — see
`anki/rslib/src/storage/topic_stats.rs:20`). No transaction, no card
mutation, so opening the score view never costs the student their undo
history (`gmat_scores.rs:85-96`).

Each score is a `ScoreValue` (`proto/anki/scheduler.proto:537`):
`abstained`, `score`, `low`, `high`, `unit`, `confidence`, `reasons`,
`missing`. When a score has too little of *its own* data it abstains: it
sets `abstained = true` and fills `missing` with what's needed instead of
producing a number (`gmat_scores.rs:416`).

---

## Give-up / abstain thresholds

Defined at `gmat_scores.rs:36-40`. Each score has an independent rule.

| Constant | Value | Score it gates |
|---|---|---|
| `MIN_MEMORY_REVIEWS` | `30` | Memory (`gmat_scores.rs:37`) |
| `MIN_PERF_ANSWERS` | `20` | Performance (`gmat_scores.rs:38`) |
| `MIN_READINESS_REVIEWS` | `200` | Readiness (`gmat_scores.rs:39`) |
| `MIN_READINESS_COVERAGE` | `0.50` (50%) | Readiness (`gmat_scores.rs:40`) |

Beyond these counts, Performance additionally abstains if all answers are
correct or all wrong (`n_correct == 0 || n_correct == n_answers`), since θ
is not estimable at the extremes (`gmat_scores.rs:187-191`). Readiness
additionally abstains if Performance itself abstained (`gmat_scores.rs:317`).

---

## 1. MEMORY score — current FSRS recall

**Question it answers:** can the student remember what they've studied *right
now*? (`memory_score`, `gmat_scores.rs:113-157`)

**Method.** Sum `total` reviews across all rows; abstain if
`< MIN_MEMORY_REVIEWS` (30) (`gmat_scores.rs:114-119`). For each card that
has an FSRS stability and a last-review time (skipping stability ≤ 0), compute
the current retrievability via the `fsrs` crate's `current_retrievability`
(`gmat_scores.rs:130-137`):

$$
\begin{aligned}
\text{days} &= \max\!\left(0,\ \frac{\text{now} - \text{last\_review}}{86{,}400{,}000}\right) \\[2pt]
\text{factor} &= 0.9^{\,1/(-\text{decay})} - 1 \\[2pt]
\text{recall} &= \left(\frac{\text{days}}{\text{stability}}\cdot \text{factor} + 1\right)^{-\text{decay}}
\end{aligned}
$$

where $\text{decay}$ is the card's decay, else the FSRS-5 default $0.5$.

(`current_retrievability` and `FSRS5_DEFAULT_DECAY = 0.5` are from
`fsrs-5.2.0/src/inference.rs:24,60-63`; `MS_PER_DAY = 86_400_000.0` at
`gmat_scores.rs:42`.) If no card has an FSRS memory state, abstain
(`gmat_scores.rs:140-142`).

**Score + range** (`gmat_scores.rs:143-156`). Take the mean and standard
deviation of the per-card recalls (`mean_sd`, `gmat_scores.rs:405`):

$$
\begin{aligned}
\text{score} &= \text{mean}\cdot 100 \\
\text{half} &= \text{sd}\cdot 100 \\
\text{low} &= \max(0,\ \text{score} - \text{half}) \\
\text{high} &= \min(100,\ \text{score} + \text{half})
\end{aligned}
$$

where $\text{half}$ is $\pm 1$ SD of per-card recall.

- **Unit:** `pct`. **Range:** 0–100, band = mean ± 1 SD (clamped to [0,100]).
- **Confidence:** empty (not reported for memory).

---

## 2. PERFORMANCE score — Rasch / 1PL ability

**Question it answers:** can the student answer a new, exam-style question?
(`PerfEstimate`, `gmat_scores.rs:163-259`)

**Item difficulty `b`.** Each answered card (`total > 0`) gets a difficulty
logit from its tags (`difficulty_logit` / `difficulty_0_100`,
`gmat_scores.rs:263-294`):

- `aidiff::NN` (AI-rated 0–100) wins when present and finite; non-finite
  values (`aidiff::nan`/`::inf`) fall through so they don't poison θ
  (`gmat_scores.rs:269-278`).
- Else coarse `difficulty::easy|medium|hard` → **20 / 50 / 80**
  (`gmat_scores.rs:279-292`).
- Else neutral **50** (`gmat_scores.rs:293`).

The 0–100 value maps to a logit with `DIFFICULTY_SCALE = 4.0`
(`gmat_scores.rs:49,264`):

$$
b = \left(\frac{\text{difficulty}}{100} - 0.5\right)\cdot 4.0
\qquad (\text{range } \pm 2 \text{ logits:}\ 0\!\to\!-2,\ 50\!\to\!0,\ 100\!\to\!+2)
$$

**θ via Newton MLE.** For $P(\text{correct}) = \sigma(\theta - b)$ with
$\sigma(x) = 1/(1+e^{-x})$ (`sigmoid`, `gmat_scores.rs:401`), $\theta$ is the
maximum-likelihood estimate over answered items, solved by Newton–Raphson from
$\theta = 0$ (`gmat_scores.rs:193-211`). For up to 50 iterations, with
$p_i = \sigma(\theta - b_i)$ per item ($c_i$ correct of $n_i$ attempts):

$$
\text{grad} = \sum_i \left(c_i - n_i\,p_i\right), \qquad
\text{info} = \sum_i n_i\,p_i\,(1 - p_i), \qquad
\theta \mathrel{+}= \frac{\text{grad}}{\text{info}}
$$

stopping when $|\text{step}| < 10^{-6}$ (or $\text{info} < 10^{-9}$), then
clamping $\theta$ to $[-6, 6]$ (`gmat_scores.rs:212`). Here $\text{info}$ is the
Fisher information.

**Standard error from Fisher information** (`gmat_scores.rs:213-220`):

$$
\text{info} = \sum_i n_i\,p_i\,(1 - p_i), \qquad
\text{se} = \frac{1}{\sqrt{\text{info}}}
$$

(info recomputed at the final $\theta$; $\text{se} = 3.0$ if $\text{info} \approx 0$).

**Score + 95% CI** (`to_score_value`, `gmat_scores.rs:240-258`). Map θ (and
its ±1.96·SE bounds) through the sigmoid to a percentage:

$$
\begin{aligned}
\text{mid} &= \sigma(\theta)\cdot 100 \\
\text{low} &= \sigma(\theta - 1.96\,\text{se})\cdot 100 \\
\text{high} &= \sigma(\theta + 1.96\,\text{se})\cdot 100
\end{aligned}
$$

- **Unit:** `pct`. **Range:** 0–100. The band is the 95% confidence interval
  ($\theta \pm 1.96\,\text{SE}$) pushed through the sigmoid — asymmetric in pct
  space, bounded by (0,100) by construction.
- **Confidence:** empty (not reported for performance).

---

## 3. READINESS score — projected GMAT 205–805

**Question it answers:** what would the student score on the exam?
(`readiness_score`, `gmat_scores.rs:300-357`)

**Coverage** (`coverage_fraction`, `gmat_scores.rs:361-371`) = fraction of the
28-entry GMAT Focus outline (`OUTLINE_TOPICS`, `gmat_scores.rs:54-83`) that any
studied card's topic tag covers. A card's topic tag is the first `::`-bearing
tag whose namespace is `Quant`/`Verbal`/`DataInsights` — an allowlist so
`id::`, `difficulty::`, `split::`, etc. are ignored (`card_topic_tag`,
`gmat_scores.rs:378-387`; `topic_covers`, `gmat_scores.rs:391-395`).

**Abstain** unless all three hold (`gmat_scores.rs:304-326`):
total reviews ≥ `MIN_READINESS_REVIEWS` (200), coverage ≥
`MIN_READINESS_COVERAGE` (0.50), and Performance did not abstain. Each unmet
condition adds its own `missing` message.

**θ → GMAT scale, discounted by coverage** (`gmat_scores.rs:328-334`). The
expected proportion-correct is pulled toward the 4-choice guess floor
(`GUESS_FLOOR = 0.25`, `gmat_scores.rs:47`) by the *uncovered* fraction, then
mapped onto the scale with `GMAT_MIN = 205.0` and `GMAT_SPAN = 600.0`
(`gmat_scores.rs:43-44`):

$$
\begin{aligned}
\text{to\_gmat}(p) &= 205 + \big[\,p\cdot c + 0.25\,(1 - c)\,\big]\cdot 600 \\
\text{score} &= \text{round}_{10}\!\big(\text{to\_gmat}(\sigma(\theta))\big)
\end{aligned}
$$

where $c$ = coverage; $205$ = `GMAT_MIN`, $600$ = `GMAT_SPAN`, and $0.25$ =
`GUESS_FLOOR` (the 4-choice guess floor).

`round_to_10` snaps to the nearest 10 (`gmat_scores.rs:412`) so scores step by
10, matching real GMAT reporting.

**Range: θ's 95% CI widened by the uncovered fraction** (`gmat_scores.rs:335-338`):

$$
\begin{aligned}
\text{widen} &= (1 - c)\cdot 60 \\
\text{low} &= \text{round}_{10}\!\big(\max(205,\ \text{to\_gmat}(\sigma(\theta - 1.96\,\text{se})) - \text{widen})\big) \\
\text{high} &= \text{round}_{10}\!\big(\min(805,\ \text{to\_gmat}(\sigma(\theta + 1.96\,\text{se})) + \text{widen})\big)
\end{aligned}
$$

`GMAT_MAX_CLAMP = GMAT_MIN + GMAT_SPAN = 805` (`gmat_scores.rs:359`). So the
band is the performance 95% CI projected onto the scale, then padded by up to
±60 points for topics not yet covered, clamped to [205, 805].

**Confidence** from coverage (`gmat_scores.rs:339-345`):

| Coverage | Confidence |
|---|---|
| ≥ 0.80 | `high` |
| ≥ 0.50 | `medium` |
| < 0.50 | `low` |

- **Unit:** `gmat`. **Range:** 205–805, in steps of 10.

---

## Constants reference

| Constant | Value | Location |
|---|---|---|
| `MIN_MEMORY_REVIEWS` | `30` | `gmat_scores.rs:37` |
| `MIN_PERF_ANSWERS` | `20` | `gmat_scores.rs:38` |
| `MIN_READINESS_REVIEWS` | `200` | `gmat_scores.rs:39` |
| `MIN_READINESS_COVERAGE` | `0.50` | `gmat_scores.rs:40` |
| `MS_PER_DAY` | `86_400_000.0` | `gmat_scores.rs:42` |
| `GMAT_MIN` | `205.0` | `gmat_scores.rs:43` |
| `GMAT_SPAN` | `600.0` (805 − 205) | `gmat_scores.rs:44` |
| `GMAT_MAX_CLAMP` | `805` (`GMAT_MIN + GMAT_SPAN`) | `gmat_scores.rs:359` |
| `GUESS_FLOOR` | `0.25` | `gmat_scores.rs:47` |
| `DIFFICULTY_SCALE` | `4.0` | `gmat_scores.rs:49` |
| coarse difficulty easy/medium/hard | `20 / 50 / 80` | `gmat_scores.rs:282-290` |
| θ clamp | `[−6.0, 6.0]` | `gmat_scores.rs:212` |
| Newton iterations (max) | `50` | `gmat_scores.rs:195` |
| CI z-score | `1.96` (95%) | `gmat_scores.rs:245-246, 337-338` |
| readiness widen per uncovered | `60` points | `gmat_scores.rs:336` |
| `FSRS5_DEFAULT_DECAY` | `0.5` | `fsrs-5.2.0/src/inference.rs:24` |
| `OUTLINE_TOPICS` count | `28` | `gmat_scores.rs:54-83` |
