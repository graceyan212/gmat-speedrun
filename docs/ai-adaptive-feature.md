# AI difficulty + computer-adaptive selection

How the app's AI feature works, how it's traceable, how it beats a baseline, how
to switch it off, and how to run/verify everything. Covers the assignment's "AI
feature + eval," the "switchable off," and the learning-science "ablation."

## 1. The AI feature — LLM difficulty calibration

Anki's stock difficulty signal for these items is a coarse 3-bucket tag
(`difficulty::easy|medium|hard` — currently 6 / 29 / 33 across 68 questions).
The AI feature replaces that with a **fine-grained 0–100 difficulty per question,
each with a one-line cited rationale**, produced by an LLM rating each item
against an explicit rubric (reasoning steps, concept load, distractor
trickiness, parsing/wording, computation weight, intended time).

**Traceable, not a black box.** Every rating is written to
`content/ai_difficulty.json` as `{id: {ai_difficulty, ai_difficulty_reason,
rubric_version}}`. Example:

```json
"Q-PS-003": { "ai_difficulty": 72,
  "ai_difficulty_reason": "n^2 divisible by 72=2^3*3^2 forces n divisible by 12; classic must-divide trap where (D)24 and (E)36 look right." }
```

All 68 questions are rated; the spread runs 16→82 (median 48) across 8 bands vs
the baseline's 3 buckets.

**Reproducible pipeline:** `content/tools/calibrate_difficulty.py` regenerates
the ratings from an `ANTHROPIC_API_KEY` (Anthropic SDK) or the local `claude`
CLI, and can apply the results as `aidiff::NN` tags onto a collection's notes
(`--apply-tags`). Tonight's ratings were produced by a fan-out of Claude agents
applying the same rubric, because no API key was available in the build
environment — the script is the canonical, re-runnable pipeline.

## 1a. Prompt-injection resistance

The item text sent to the model is **untrusted** (paraphrases, seed/external
content), so the calibration pipeline is hardened against a poisoned item that
tries to hijack its own rating. Defence in depth, in `calibrate_difficulty.py`:

- **Isolate untrusted content (input side).** `build_prompt()` wraps the item in
  an `<ITEM>…</ITEM>` fence introduced by an explicit note that the fenced text is
  *data, not instructions*, and `_neutralize()` defangs any attempt to forge the
  fence markers from inside the content — so injected "ignore the rubric…" text
  lands as data to be rated, not as a command.
- **Validate the output (output side).** `_parse_rating()` accepts **only** a
  well-formed `{"ai_difficulty": 0–100, "ai_difficulty_reason": …}` object; it
  rejects non-JSON, missing/non-numeric scores, and out-of-range values, drops
  extra keys, and caps the reason. A poisoned item therefore cannot push the
  stored rating outside 0–100 or smuggle content into the sidecar.
- **Proof (no model call).** `content/tools/injection_test.py` feeds a poisoned
  item (injected instructions + a forged `</ITEM>` + an out-of-range demand) and
  asserts both guards hold: `python content/tools/injection_test.py` (exit 0).

## 2. The eval — does AI difficulty beat the coarse baseline?

`content/tools/eval_difficulty.py` scores both difficulty models against the
student's own answers:
- Join each review to its item (`id::` tag) → AI difficulty + coarse difficulty.
- Fit a 1-parameter Rasch ability θ on the first 70% of answers (by time), once
  per model.
- On the held-out last 30%, predict `P(correct)=σ(θ − b)` and score **Brier**
  (lower = better calibrated) and **accuracy**. Lower held-out Brier for AI ⇒
  AI difficulty predicts the student's performance better ⇒ it beats the baseline.

**Current result (252 usable reviews).** On the time-based split AI difficulty
and the coarse baseline **tie** (held-out Brier 0.1740 vs 0.1744) — but that split
shares items between train and held-out. On a **leakage-free item-disjoint split**
(no shared items) AI **beats** the baseline (0.1706 vs 0.1720, **+0.0014**);
accuracy ties at 80%. The win is in **calibration**, not classification — a real
but honestly **small and sample-dependent** edge (the Friday snapshot at 174
reviews showed +0.0079). See `RESULTS.md` §2, §2.1 (leakage check), and §3
(calibration curve) for the full tables. An earlier audit at ~41 sync-test
answers (almost all "correct", θ ≈ +3.5) showed no signal.

**Harness validated:** `content/tools/eval_selftest.py` runs the same
Brier/accuracy machinery on synthetic answers *with* signal (mid-ability students
who miss hard items). Over 30 simulated students it correctly rates a
fine-grained (AI-like) difficulty **below** the coarse baseline (mean Brier
lower; AI wins 23/30). So the tie is a data limitation, not a broken eval.

## 3. Computer-adaptive selection (the learning-science feature)

`anki/rslib/src/scheduler/adaptive.rs` (shared engine → desktop **and** phone):
- Estimates the student's ability **θ** with a Rasch/1PL Newton MLE over their
  answered items joined with each item's difficulty (`aidiff::`, falling back to
  coarse). Difficulty maps to a logit `b=(d/100−0.5)·4`.
- Picks the card whose difficulty is **nearest θ** — harder when you're doing
  well, easier when you're struggling — as a **secondary sort key** *within* the
  existing points-at-stake weakest-topic order (weakness sets the neighbourhood,
  difficulty-fit picks the card).
- **Read-only / undo-safe:** a single aggregate query, no transaction, no card
  writes — building the queue never costs the student their pending undo (tested).
- Ships to the phone for free via the shared rslib (same mechanism as the Jul-1
  ordering); no phone-side code change needed.

**Shared ability estimator.** `estimate_ability() → {theta, standard_error}` is
`pub(crate)` so the Performance score (`scheduler::gmat_scores`) and this selector
use **one** θ — the score you see and the card it picks can't drift. The standard
error (1/√Fisher information) gives the Performance score its range.

## 4. Switchable off + the ablation

The feature is gated behind the collection config flag
`BoolKey::GmatAdaptiveEnabled` (default **off**). Off ⇒ pure Jul-1 weakness
ordering; on ⇒ weakness + difficulty-fit. This flag is exactly the ablation's arms:

| Arm | Config |
|---|---|
| Plain Anki | no GMAT ordering (stock scheduler) |
| Adaptive **off** | points-at-stake weakness ordering only (`GmatAdaptiveEnabled=false`) |
| Adaptive **on** | weakness + AI-difficulty fit (`GmatAdaptiveEnabled=true`) |

Method: run each arm over a study session (or a simulated student) and compare how
quickly cards converge to the student's ability / how well time is spent on
near-ability items. The `estimate_ability` θ + the eval's Brier are the
measurement tools. **The run and writeup are done — see `ABLATION.md` and
`RESULTS.md` §4** (a simulation, honestly labelled as such: it shows the mechanism
works as designed, not a human learning-gain trial).

## 5. Run / verify

```bash
# regenerate AI difficulty (needs ANTHROPIC_API_KEY or the claude CLI):
python content/tools/calibrate_difficulty.py
python content/tools/calibrate_difficulty.py --dry-run          # preview prompts, no model call
python content/tools/calibrate_difficulty.py --apply-tags COL.anki2   # write aidiff:: tags

# eval AI difficulty vs coarse baseline + the train/test LEAKAGE CHECK:
python content/tools/eval_difficulty.py /path/to/collection.anki2

# prompt-injection resistance of the calibration pipeline (no model call):
python content/tools/injection_test.py

# validate the eval harness on synthetic signal (exit 0 = valid):
python content/tools/eval_selftest.py

# adaptive engine tests (from anki/):
PROTOC=$PWD/out/extracted/protoc/bin/protoc cargo test -p anki adaptive
```

(All Python here runs under the project's env, e.g.
`PYTHONPATH=anki/out/pylib anki/out/pyenv/bin/python …`.)

## 6. Files

| File | Role |
|---|---|
| `content/ai_difficulty.json` | traceable AI ratings (id → difficulty + rationale) |
| `content/tools/calibrate_difficulty.py` | reproducible calibration pipeline + `--apply-tags` |
| `content/tools/eval_difficulty.py` | held-out Brier/accuracy, AI vs coarse baseline |
| `content/tools/eval_selftest.py` | synthetic validation of the eval harness |
| `content/tools/injection_test.py` | prompt-injection resistance test (input isolation + output guard) |
| `anki/rslib/src/scheduler/adaptive.rs` | Rasch θ + difficulty-fit selection, shared `estimate_ability` |
| `anki/rslib/src/config/bool.rs` | `GmatAdaptiveEnabled` toggle |
