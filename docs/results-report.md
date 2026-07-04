# Results report — GMAT-on-Anki

Consolidated evidence for the submission: model descriptions, eval results,
what worked, what didn't, and what's still pending data. Numbers are current as
of Fri Jul 3. Feature details: `ai-retrieval-feature.md`, `ai-adaptive-feature.md`.

## Models (descriptions)

| Model | What it is | Where |
|---|---|---|
| **Memory** | FSRS recall/retention (Anki's engine) → a memory score | Jul-1 build |
| **Performance** | Rasch/1PL ability **θ** estimated from right/wrong vs item difficulty; shared `estimate_ability()` returns θ + standard error (→ range) | anki `main` (scores chat wires the display) |
| **Readiness** | θ mapped to a GMAT-style scale, coverage-discounted, with a confidence range + give-up rule (≥200 reviews, ≥50% coverage) | `gmat_readiness.py` |
| **AI difficulty** | LLM rates each question 0–100 with a cited rationale (traceable); feeds adaptive selection + performance | `content/ai_difficulty.json` |
| **AI retrieval** | LLM reranks candidate sources by shared method; cited output | `content/retrieval_ai.json` |

## Eval results

### AI retrieval — beats keyword & vector search ✅ (real, held-out)
Leave-one-out over 68 questions; ground truth = same topic (same method); the AI
saw only candidate content (no topic labels).

| method | acc@1 | acc@3 | wrong@1 |
|---|---|---|---|
| keyword (BM25) | 55.4% | 76.9% | 44.6% |
| vector (TF-IDF cosine) | 53.8% | 73.8% | 46.2% |
| **AI reranker** | **73.8%** | **89.2%** | **26.2%** |

**AI beats both baselines by ~18–20 pts top-1 accuracy and roughly halves the
wrong-answer rate.** (n = 65; 3 single-item topics excluded for all methods.)

### AI difficulty vs coarse baseline — inconclusive on current data (honest)
`eval_difficulty.py` (held-out Brier + accuracy, AI difficulty vs the coarse
`difficulty::` tags) currently **ties**: the only real answers so far came from
sync testing and were almost all "correct" (θ ≈ +3.5), so there is no right/wrong
signal for any difficulty model to separate on. **This did not work yet — and
that's a data limitation, not a broken eval:** `eval_selftest.py` runs the same
machinery on synthetic answers *with* signal and, over 30 simulated students,
correctly rates the finer AI difficulty below the coarse baseline (mean Brier
lower; AI wins 23/30). A real number needs a genuine study session with misses.

## Pending evidence (needs real data or another stream)
- **Memory-model calibration chart + Brier/log-loss on held-out reviews** — needs
  genuine review data (current reviews are all "correct").
- **Performance-model accuracy on held-out questions** — harness ready; needs
  real answers + the scores chat's performance model wired in.
- **Ablation (adaptive on / off / plain Anki, equal study time)** — the toggle
  (`GmatAdaptiveEnabled`) enables the three arms; the run + writeup is pending a
  framing decision (adaptive targets nearest-ability ~50% success, vs the
  learning literature's ~85%) and ideally real study data.

## What worked / what didn't (honest summary)
- **Worked:** two-way sync (verified on real devices, no loss/double, conflict
  rule documented); offline review; the **AI retrieval feature clearly beats both
  baselines**; the adaptive engine (Rasch θ + difficulty-fit, undo-safe, 7 tests).
- **Didn't (yet):** the AI-difficulty eval is a tie on current data (no answer
  signal) — validated the harness synthetically and flagged it honestly.
- **Not started / gated externally:** memory calibration chart, performance
  accuracy, ablation run, phone score UI (scores chat), packaged builds,
  recordings, Brainlift.
