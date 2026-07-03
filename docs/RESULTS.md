# Results

Honest results report for the GMAT-on-Anki submission. Every number below
comes from a run that is reproducible on disk; nothing here is estimated or
projected. Where a result is a simulation rather than a measurement, it is
labelled as such.

---

## 1. The three models

The app reports three deliberately distinct measurements, each with its own
give-up rule. Full formulas, constants, and source line references are in
[`docs/SCORE_MAPPING.md`](SCORE_MAPPING.md).

| Score | Model | What it measures |
|---|---|---|
| **Memory** | FSRS current retrievability | Can the student recall studied material *right now*? Mean ± 1 SD of per-card FSRS recall, on 0–100. |
| **Performance** | Rasch / 1PL ability (θ) | Can the student answer a new exam-style item? Newton-MLE θ over answered items, mapped through the sigmoid to 0–100 with a 95% CI. |
| **Readiness** | θ → GMAT 205–805 scale | What would the student score on the exam? σ(θ) discounted by topic coverage, mapped onto 205–805 (`GMAT_MIN = 205`, `GMAT_SPAN = 600`, clamp 805), stepped to the nearest 10. |

The Performance θ estimator is the same Rasch/1PL solver the adaptive
card-selection feature uses, so the score and the study feature share one
model. See `SCORE_MAPPING.md` §2–3 for the exact math.

---

## 2. Performance / difficulty evaluation

The Performance score rides on item difficulty. The question this eval asks is:
does the **AI-rated** difficulty (`aidiff::NN`, 0–100) predict pass/fail better
than the **coarse** three-bucket tag (`difficulty::easy|medium|hard` → 20/50/80)?

Run against the real collection:

- Reviews in log: **183**
- Usable (have both an `id::` tag and an AI rating): **174**
- Train / held-out split (0.7): **train = 121**, **held-out = 53**

Held-out results:

| Difficulty source | Held-out Brier (lower better) | Held-out accuracy |
|---|:---:|:---:|
| **AI difficulty** | **0.1132** | **87%** |
| Coarse baseline | 0.1211 | 87% |

**AI difficulty beats the coarse baseline on Brier by +0.0079. Accuracy ties at
87%.**

Honest framing: the win is in **calibration** (Brier), not in raw accuracy, and
the margin is **modest**. The finer AI ratings produce probability estimates
that are slightly closer to observed outcomes, but they do not flip a
meaningful number of pass/fail predictions at this sample size.

---

## 3. Calibration (from the run)

Reliability diagram for the AI-difficulty Rasch model on the 53 held-out
reviews. Exact stdout from the run (identical under both the repo pyenv Python
and the system `python3`):

```
reviews in log: 183 | usable (have id::+ai): 174
  skipped: no id:: tag=0, no AI rating=9
  train=121  held-out=53  (split=0.7)

Reliability table (AI-difficulty Rasch model, held-out reviews)
  fitted theta (train) = +2.350 | held-out n = 53

  bucket        n   mean_pred   observed   gap
  ----------  ---   ---------   --------   ------
  [0.0,0.1)     0          -          -        -
  [0.1,0.2)     0          -          -        -
  [0.2,0.3)     0          -          -        -
  [0.3,0.4)     0          -          -        -
  [0.4,0.5)     0          -          -        -
  [0.5,0.6)     0          -          -        -
  [0.6,0.7)     0          -          -        -
  [0.7,0.8)     0          -          -        -
  [0.8,0.9)    12     0.868      0.833   -0.034
  [0.9,1.0)    41     0.934      0.878   -0.056

  Overall Brier score = 0.1132   (0 = perfect, lower is better)
  Overall log loss    = 0.3815   (0 = perfect, lower is better)
```

- **Overall Brier = 0.1132**, **log loss = 0.3815**, fitted θ (train) = **+2.350**.
- All 53 held-out predictions land in the two top buckets — the fitted learner
  is strong, so the model rarely predicts a miss. In both buckets the model is
  slightly **over-confident** (observed a touch below predicted: gaps −0.034 and
  −0.056), i.e. it predicts pass a bit more often than the student actually
  passes.

**Chart.** `matplotlib` was unavailable in both interpreters:

```
(matplotlib unavailable: ModuleNotFoundError: No module named 'matplotlib')   [repo pyenv]
(matplotlib unavailable: ImportError: numpy.core.multiarray failed to import) [system python3]
```

The script fell back to a self-contained SVG reliability diagram, written to
[`docs/calibration.html`](calibration.html) (open in a browser). Its Brier,
log loss, θ, and per-bucket numbers match the stdout above exactly.

---

## 4. Ablation: does the adaptive study feature help?

**This is a SIMULATION, not a human trial.** A simulated learner with a fixed
*true* θ answers items under three item-selection policies at an **equal study
budget** (same attempts, same seeds). Full method and caveats:
[`docs/ABLATION.md`](ABLATION.md).

Setup:

- Deck: **369 items** with difficulty, mean **52.17** / sd **21.0** on the
  0–100 scale (range 16–82), logit `b` in **[−1.36, +1.28]** via
  `b = (d/100 − 0.5) · 4.0`.
- Budget **N = 60** attempts/arm, **200** seeds, desirable band
  `|b − true_theta| < 1.0`, `SCALE = 4.0`.

Results (mean ± sd over 200 seeds):

| Arm | % items in desirable band (higher better) | θ error `|θ̂ − true_θ|` @ N (lower better) |
|---|:---:|:---:|
| **ADAPTIVE** | **95.0% ± 8.3%** | **0.212 ± 0.161** |
| OFF | 47.5% ± 24.1% | 0.271 ± 0.189 |
| PLAIN | 46.1% ± 22.8% | 0.292 ± 0.251 |

- **Targeting:** ADAPTIVE keeps **+48.9 percentage points** more items
  well-targeted than PLAIN (plain-Anki order), for the same study budget.
- **Ability recovery:** ADAPTIVE's θ error (0.212) is **~28% lower** than
  PLAIN's (0.292).
- Predicted ordering — ADAPTIVE best on both metrics, OFF and PLAIN close
  behind — **holds**. The run is deterministic; a re-run produced identical
  numbers.

Machine-readable summary from the run:

```json
{"n_items":60,"n_seeds":200,"band":1.0,"scale":4.0,"deck_n":369,"deck_mean_diff":52.17,"arms":{"ADAPTIVE":{"band_pct_mean":95.0,"band_pct_sd":8.34,"theta_err_mean":0.212,"theta_err_sd":0.1614},"OFF":{"band_pct_mean":47.55,"band_pct_sd":24.09,"theta_err_mean":0.2711,"theta_err_sd":0.1892},"PLAIN":{"band_pct_mean":46.1,"band_pct_sd":22.81,"theta_err_mean":0.2924,"theta_err_sd":0.2508}}}
```

---

## 5. Give-up rule and "still scores with AI off"

**Give-up / abstain.** Each score refuses to invent a number when it lacks its
own data (`SCORE_MAPPING.md` §give-up). Thresholds: Memory needs ≥ 30 reviews,
Performance ≥ 20 answers, Readiness ≥ 200 reviews **and** ≥ 50% topic coverage
**and** a non-abstaining Performance score. Performance additionally abstains
at the extremes (all-correct or all-wrong), where θ is not estimable. When a
score abstains it reports what is still missing instead of a number.

**Both apps still score with AI off.** The adaptive study feature is a
**toggle, off by default**. With AI difficulty absent, the difficulty pipeline
falls back to the coarse `difficulty::easy|medium|hard` → 20/50/80 tags (and
neutral 50 if even those are missing), so all three scores still compute. AI
ratings *improve calibration* (§2–3); they are not *required* to produce a
score. The ablation's OFF and PLAIN arms are exactly this AI-off / non-adaptive
path, and both still recover θ.

---

## 6. What didn't work / honest notes

- **The eval margin is modest, and accuracy ties.** AI difficulty beats the
  coarse baseline on Brier by only **+0.0079**, and **accuracy ties at 87%**.
  The only real win is **calibration**, not classification. Don't oversell it.
- **The calibration lives in the top two buckets only.** Because the fitted
  learner is strong (θ = +2.350), all 53 held-out predictions sit at P ≥ 0.8;
  there is **no signal about calibration at low predicted probabilities**. The
  model is also slightly over-confident in both populated buckets.
- **An earlier audit showed no signal.** Run before enough reviews had
  accumulated, the difficulty comparison had too little data to separate AI
  from coarse. The +0.0079 Brier edge only appeared once the log reached 174
  usable reviews.
- **The ablation is a SIMULATION, not a human 3-build trial.** Answers are
  generated by the same Rasch/1PL model the estimator inverts, so the in-band
  metric is favorable to ADAPTIVE almost by construction. It demonstrates the
  *mechanism* works as designed; it is **not** evidence of a learning gain for
  real students. The intended real test — three parallel builds, equal study
  time, real learners — was out of scope solo.
- **Difficulty data is coarse.** Only 68 of 369 items are finely AI-rated; the
  rest sit at exactly 20/50/80, so the effective difficulty grid is lumpy and
  the deck's ability spread is narrow (`b` ≈ [−1.4, +1.3]). Absolute band
  percentages would shift with a fully AI-rated deck.

---

## Reproduce

- Calibration: writes the stdout above and `docs/calibration.html`.
- Ablation: `cd /Users/graceyan/Desktop/alpha/speedrun && python3 content/tools/ablation.py` (deterministic; numbers reproduce exactly).

See [`docs/SCORE_MAPPING.md`](SCORE_MAPPING.md) and [`docs/ABLATION.md`](ABLATION.md)
for full methods, constants, and caveats.
