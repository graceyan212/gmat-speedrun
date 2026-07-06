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
| **Readiness** | θ → GMAT 205–805 scale | What would the student score on the exam? $\sigma(\theta)$ discounted by topic coverage, mapped onto 205–805 (`GMAT_MIN = 205`, `GMAT_SPAN = 600`, clamp 805), stepped to the nearest 10. |

The Performance θ estimator is the same Rasch/1PL solver the adaptive
card-selection feature uses, so the score and the study feature share one
model. See `SCORE_MAPPING.md` §2–3 for the exact math.

---

## 2. Performance / difficulty evaluation

The Performance score rides on item difficulty. The question this eval asks is:
does the **AI-rated** difficulty (`aidiff::NN`, 0–100) predict pass/fail better
than the **coarse** three-bucket tag (`difficulty::easy|medium|hard` → 20/50/80)?

Run against the real collection (it has grown as studying continued):

- Reviews in log: **286**
- Usable (have an `id::` tag, an AI rating, and a coarse tag): **252**
- Train / held-out split (0.7, by time): **train = 176**, **held-out = 76**

Held-out results (time-split):

| Difficulty source | Held-out Brier (lower better) | Held-out accuracy |
|---|:---:|:---:|
| **AI difficulty** | **0.1740** | 80% |
| Coarse baseline | 0.1744 | 80% |

On this time-based split the two are effectively **tied** (Brier +0.0004). At the
Friday snapshot (174 usable reviews) AI led by +0.0079; as the sample grew, that
edge shrank into the noise. **But the time-split shares items between train and
held-out**, so the honest comparison is the leakage-free one in §2.1 — and there
AI still leads.

Honest framing: any edge is in **calibration** (Brier), not accuracy, and it is
**small and sample-dependent** — don't oversell it. The finer AI ratings give
slightly better-calibrated probabilities; they do not flip a meaningful number of
pass/fail predictions at this sample size.

---

## 2.1 Leakage check (train/test hygiene)

Because the "AI difficulty predicts answers" claim is only as good as its
train/test split, `eval_difficulty.py` runs an explicit leakage check as its own
step. Verbatim from the run:

```
--- Leakage check (train/test hygiene) ---
  1. content-only difficulty: AI ratings come from the item text +
     rubric (calibrate_difficulty.py), computed with NO access to the
     revlog, so `b` can't encode held-out outcomes (no target leakage).
  2. time-split item overlap: 28 of 28 held-out
     items were also seen in train (the 1-parameter Rasch has no
     per-item weight, but the disjoint check below removes it anyway).
  3. item-disjoint split: 32 train items (181 reviews),
     15 held items (71 reviews) — ZERO shared items.
            theta   held-out Brier   held-out accuracy
  AI diff   +2.16   0.1706          80%
  coarse    +2.68   0.1720          80%
  Brier(coarse) - Brier(AI) = +0.0014  ->  AI difficulty beats the coarse baseline
```

1. **No target leakage.** Item difficulties come from the LLM reading item text +
   the rubric only (`calibrate_difficulty.py`), with **zero access to the
   revlog** — so the predictor `b` cannot encode the labels it is later scored
   against.
2. **The time-split shares items.** All 28 held-out items were also seen in
   train. A single-scalar Rasch has no per-item weight to memorise, but we don't
   lean on that —
3. **Item-disjoint split.** Re-partition *by item* (paraphrases collapsed to
   their base id) so train and held-out share **no item at all**: AI difficulty
   **still beats** the coarse baseline (Brier **0.1706 vs 0.1720, +0.0014**). The
   small calibration edge is therefore **not an artifact of shared items**.

Reproduce: `python content/tools/eval_difficulty.py <collection.anki2>` prints the
headline result followed by this leakage check.

### 2.1.1 Text-similarity near-copy scan (rubric 7e)

The check above is **ID-based**: it collapses paraphrase ids (`-pN`) to a base id
so a paraphrase can't straddle the split. That is blind to a near-copy filed
under an *unrelated* id. `content/tools/leakage_scan.py` closes that gap by
scanning the actual **text** of every training item against every test item,
independent of ids — a direct answer to rubric 7e: *"scan your TRAINING data and
flag any TEST item, or a NEAR-COPY of one, that slipped in."*

**Scope.** Every item in `content/items.json` carries a `split`; paraphrases
inherit their parent's split. TRAIN = `split == train`; TEST = `split ∈
{test, holdout, gold}` (everything **not** used for training), plus the
`gold_set` probes. Every **train × test** pair is compared (364 × 117 = **42,588
pairs**).

**Metric.** On normalized text (stem + choices + answer, lowercased, punctuation
stripped, whitespace collapsed) we compute **both** `difflib.SequenceMatcher`
ratio (character-level, order-sensitive) **and token Jaccard** (order-free) and
take the **max**, so a match on *either* trips the flag at **threshold 0.85**.
Two normalization details keep it honest on a formulaic math bank: (i) the 19
Data-Sufficiency items share one of two identical ~300-char answer-key templates
("Statement (1) ALONE is sufficient…"); that boilerplate is stripped so it can't
manufacture ~0.9 similarity between two unrelated DS questions. (ii) A flagged
cross pair counts as a **verbatim leak** only if the two are the *same question*
(stem-only similarity ≥ 0.97 **and** matching answer, or an exact duplicate);
otherwise it is a **template-sibling** (same solution schema, different
numbers/answer).

**Result — CLEAN.** Verbatim output:

```
(a) INTENTIONAL same-base paraphrase pairs (expected, NOT leakage):
      across train x test boundary : 0
      total anywhere in the bank   : 15
(b) CROSS-ITEM VERBATIM LEAKS (train x test, DIFFERENT base id,
    same question: near-identical stem AND same answer) — MUST be 0:
      verbatim leaks (>= threshold): 0
      exact normalized duplicates  : 0
(c) TEMPLATE-SIBLINGS above threshold (same solution schema, DIFFERENT
    numbers and answer — reported for transparency, NOT leakage):
      count: 3
        - TRAIN Q-PS-010-p2 (ans E)  ~  TEST BG-0143 (ans C)  (max=0.908; different answer)
        - TRAIN BG-0056 (ans B)  ~  TEST Q-PS-003 (ans C)  (max=0.8692; different answer)
        - TRAIN BG-0056 (ans B)  ~  TEST Q-PS-003-p2 (ans B)  (max=0.854; same answer, stem numbers differ)

RESULT: CLEAN — 0 verbatim test items (or copies) leaked into train.
```

- **0 verbatim leaks, 0 exact cross-duplicates, 0 same-base paraphrases across
  the split** — no test item, or a copy of one, is in the training data.
- The **3 template-siblings** are reported transparently and are **not** leakage:
  each is a *different question* with different numbers and an
  independently-derived answer (e.g. TRAIN `n²÷900 → 30` vs TEST `n²÷72 → 12`).
  GMAT is built from a finite set of question templates, so template overlap
  between any two large sets is unavoidable; solving the training item does **not**
  reveal the test item's answer. For context, deliberate same-base paraphrases in
  this bank reach 0.93–1.0 similarity, so 0.85–0.91 template-siblings sit inside
  the normal similarity range — hence they are flagged for the reader, not treated
  as leaks. At a stricter `--threshold 0.9` only one template-sibling remains and
  verbatim leaks stay **0**.
- The detector is not a rubber-stamp: planting an exact copy (and, separately, a
  perturbed near-copy) of a test item into train makes it report the leak and exit
  non-zero.

Reproduce: `python content/tools/leakage_scan.py` (stdlib only — no deps; exits
non-zero if any verbatim leak is found). `--json` prints a machine-readable
summary; `--top N` lists the N closest cross-item pairs; `--threshold T` adjusts
the flag cutoff.

---

## 3. Calibration (from the run)

Reliability diagram for the AI-difficulty Rasch model on the 76 held-out
reviews. Exact stdout from the run:

```
reviews in log: 286 | usable (have id::+ai): 252
  skipped: no id:: tag=0, no AI rating=34
  train=176  held-out=76  (split=0.7)

Reliability table (AI-difficulty Rasch model, held-out reviews)
  fitted theta (train) = +2.155 | held-out n = 76

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
  [0.8,0.9)    25     0.859      0.800   -0.059
  [0.9,1.0)    51     0.933      0.804   -0.129

  Overall Brier score = 0.1740   (0 = perfect, lower is better)
  Overall log loss    = 0.5828   (0 = perfect, lower is better)
```

- **Overall Brier = 0.1740**, **log loss = 0.5828**, fitted θ (train) = **+2.155**.
- All 76 held-out predictions land in the two top buckets — the fitted learner
  is strong, so the model rarely predicts a miss. In both buckets the model is
  **over-confident** (observed below predicted: gaps −0.059 and −0.129), i.e. it
  predicts pass more often than the student actually passes — more so than at the
  Friday snapshot, as the larger sample added misses the strong-θ model didn't
  expect.

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
  0–100 scale (range 16–82), logit $b$ in $[-1.36, +1.28]$ via
  $b = (d/100 - 0.5)\cdot 4.0$.
- Budget **N = 60** attempts/arm, **200** seeds, desirable band
  $|b - \theta_{\text{true}}| < 1.0$, `SCALE = 4.0`.

Results (mean ± sd over 200 seeds):

| Arm | % items in desirable band (higher better) | $\theta$ error $\lvert\hat\theta - \theta_{\text{true}}\rvert$ @ N (lower better) |
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

## 4.1 Paraphrase test: is Performance just copying Memory?

**Rubric 7d.** For ~30 cards with 2+ reworded exam-style questions each, compare
recall on the memorized card with accuracy on the reworded questions; if the two
are basically equal, the performance model is just echoing the memory model.
Full method and caveats: [`docs/PARAPHRASE_TEST.md`](PARAPHRASE_TEST.md).

Corpus: **29 source cards**, **83 paraphrases**, authored in
[`content/items.json`](../content/items.json). **This is a labelled SIMULATION,
not real students** — both signals are *derived* from the app's Rasch/1PL model
(port of `adaptive.rs` via `ablation.py`), under one stated assumption (a
familiarity bonus on the memorized card). Result (verbatim from the run):

| Signal | Mean |
|---|:---:|
| Memory recall (original card) | 86.8% |
| Paraphrase accuracy (new wording) | 71.3% |
| **Gap (paraphrase − memory)** | **−0.155** |

The gap is clearly **nonzero and negative** → reworded questions are harder than
raw recall, so Performance is **not** an echo of Memory. Had the gap been ≈ 0
the honest conclusion would be the reverse. The size of the gap is a model
output (not tuned); its sign follows from the familiarity-bonus assumption. See
[`docs/PARAPHRASE_TEST.md`](PARAPHRASE_TEST.md) for the per-section rollup, the
small-sample noise notes, and how a real `responses.json` would drop in with no
code change.

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

- **The eval margin is small and sample-dependent.** On the current 252-review
  sample the time-split is a **tie** (Brier +0.0004) and the leakage-free
  item-disjoint split (§2.1) gives AI a **+0.0014** edge; **accuracy ties at
  80%**. Any win is in **calibration**, not classification. Don't oversell it.
- **The calibration lives in the top two buckets only.** Because the fitted
  learner is strong (θ = +2.155), all 76 held-out predictions sit at P ≥ 0.8;
  there is **no signal about calibration at low predicted probabilities**, and
  the model is **over-confident** in both populated buckets (gaps −0.059, −0.129).
- **The edge moved as data accumulated.** An earlier audit (~41 sync-test
  answers) showed no signal; at the Friday snapshot (174 reviews) AI led by
  +0.0079; on the current 252-review sample the time-split is a tie and the
  leakage-free edge is +0.0014. The honest read: a small, real calibration edge
  that isn't robust enough to headline.
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

- Difficulty eval + **leakage check** (§2, §2.1):
  `python content/tools/eval_difficulty.py <collection.anki2>`
- **Train/test near-copy scan** (§2.1.1, rubric 7e — text-similarity, no deps):
  `python content/tools/leakage_scan.py`
- Calibration reliability (§3) + `docs/calibration.html`:
  `python content/tools/calibration.py <collection.anki2>`
- Ablation (§4, deterministic — numbers reproduce exactly):
  `python content/tools/ablation.py`
- Paraphrase test (§4.1, rubric 7d — memory vs performance gap, deterministic):
  `python content/scripts/gen_paraphrase_responses.py && python content/scripts/memory_vs_performance.py content/responses.json`
- Eval-harness self-test on synthetic signal: `python content/tools/eval_selftest.py`
- **Prompt-injection resistance** of the calibration pipeline (no model call):
  `python content/tools/injection_test.py`

See [`docs/SCORE_MAPPING.md`](SCORE_MAPPING.md) and [`docs/ABLATION.md`](ABLATION.md)
for full methods, constants, and caveats.
