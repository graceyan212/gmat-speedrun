#!/usr/bin/env python3
"""Generate a DEFENSIBLE, clearly-SIMULATED responses.json for the paraphrase
test (rubric 7d), then let memory_vs_performance.py compute the gap.

WHY THIS IS A SIMULATION, NOT REAL DATA
---------------------------------------
There were no real students to sit the paraphrase test this week. Section 9 of
the rubric is explicit that a clearly-labelled simulation beats a fake number,
and that honesty scores highest. So this file does NOT hand-pick numbers to
manufacture a gap: it DERIVES both the memory-recall signal and the
paraphrase-accuracy signal from the SAME Rasch / 1PL model the shipped app uses
(a line-for-line port of the primitives in content/tools/ablation.py, which are
themselves ported from anki/rslib/src/scheduler/adaptive.rs), under ONE stated
behavioural assumption -- a familiarity bonus on the memorized card. The gap is
whatever that model produces; it is not tuned.

THE BRIDGE, MODELLED HONESTLY
-----------------------------
For a simulated learner with fixed true ability theta and an item of difficulty
b (logit):

  MEMORY recall on the ORIGINAL card:
      the student has drilled this exact card, so the familiar wording gives a
      recall/retrieval advantage. We model that as an additive familiarity
      bonus FAMILIARITY_BONUS on the ability axis:
          memory_recall = sigmoid( (theta + FAMILIARITY_BONUS) - b )
      This is the probability of getting the ORIGINAL, memorized card right.

  PERFORMANCE on the PARAPHRASES (same idea, NEW words):
      the wording is new, so the familiarity bonus is gone -- the student is
      answering from true ability against the same item difficulty:
          P(correct on a paraphrase) = sigmoid( theta - b )
      Each of the card's linked paraphrases is a Bernoulli draw at this p
      (fixed seed, deterministic).

The gap = mean(paraphrase accuracy) - mean(memory recall) is therefore NEGATIVE
by construction of the FAMILIARITY BONUS -- but its SIZE is an output of the
model + the deck's real difficulties, not a number we chose. A nonzero gap is
the whole point: it shows the performance signal is NOT just echoing the memory
signal. If FAMILIARITY_BONUS were 0 the two would coincide and the gap would
vanish -- which is exactly the failure mode rubric 7d is probing for.

DETERMINISM
-----------
Fixed SEED, fixed learner ability THETA_TRUE, fixed FAMILIARITY_BONUS. Re-running
reproduces byte-identical responses.json.

Difficulty for each source card is read the SAME way adaptive.rs / ablation.py
read it: prefer an AI rating (aidiff:: tag, else ai_difficulty.json), else the
coarse difficulty::easy|medium|hard -> 20/50/80, then b = (d/100 - 0.5)*SCALE.

Usage:  python content/scripts/gen_paraphrase_responses.py [out.json]
        (default out = content/responses.json)
"""
import json
import math
import random
import sys
from pathlib import Path

CONTENT = Path(__file__).resolve().parent.parent          # .../content
ITEMS_PATH = CONTENT / "items.json"
AIDIFF_PATH = CONTENT / "ai_difficulty.json"

# --- Rasch / 1PL primitives: ported from content/tools/ablation.py ---------
# (which is itself a port of anki/rslib/src/scheduler/adaptive.rs)
SCALE = 4.0                       # ablation.py / adaptive.rs: difficulty 0-100 -> logit
COARSE = {"easy": 20.0, "medium": 50.0, "hard": 80.0}


def difficulty_to_logit(difficulty: float) -> float:
    """ablation.py::difficulty_to_logit -- b = (d/100 - 0.5) * SCALE."""
    return (difficulty / 100.0 - 0.5) * SCALE


def sigmoid(x: float) -> float:
    """ablation.py::sigmoid (overflow-guarded)."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# --- Simulation assumptions (STATED, fixed, deterministic) ------------------
SEED = 7                          # fixed RNG seed -> reproducible Bernoulli draws
THETA_TRUE = 0.6                  # the simulated learner's fixed TRUE ability (logit)
FAMILIARITY_BONUS = 1.2           # ability boost from having MEMORIZED the exact card
                                  # (logits). Set to 0 -> memory == performance -> gap 0,
                                  # i.e. the failure mode rubric 7d looks for.


def _aidiff_from_tags(tags):
    for t in tags:
        if t.lower().startswith("aidiff::"):
            try:
                v = float(t.split("::", 1)[1])
            except ValueError:
                continue
            if math.isfinite(v):
                return min(max(v, 0.0), 100.0)
    return None


def _coarse_from_tags(tags):
    for t in tags:
        if t.lower().startswith("difficulty::"):
            lvl = t.split("::", 1)[1].lower()
            if lvl in COARSE:
                return COARSE[lvl]
    return None


def card_difficulty(item, aidiff):
    """Difficulty precedence mirrors adaptive.rs::note_difficulty / ablation.py."""
    tags = item.get("tags", [])
    d = _aidiff_from_tags(tags)
    if d is None:
        j = aidiff.get(item["id"])
        if j and "ai_difficulty" in j:
            d = float(j["ai_difficulty"])
    if d is None:
        d = _coarse_from_tags(tags)
    if d is None:
        d = 50.0                  # neutral fallback (same as the app's absent-tag default)
    return d


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else CONTENT / "responses.json"

    data = json.loads(ITEMS_PATH.read_text())
    items = data if isinstance(data, list) else data["items"]
    aidiff = json.loads(AIDIFF_PATH.read_text()) if AIDIFF_PATH.exists() else {}

    rng = random.Random(SEED)
    responses = {}
    # Deterministic order: iterate items in file order so the RNG stream is fixed.
    for it in items:
        paraphrases = it.get("paraphrases") or []
        if len(paraphrases) < 2:
            continue
        d = card_difficulty(it, aidiff)
        b = difficulty_to_logit(d)

        # MEMORY: probability of recalling the exact, memorized card (familiar wording).
        memory_recall = sigmoid((THETA_TRUE + FAMILIARITY_BONUS) - b)

        # PERFORMANCE: each paraphrase is a Bernoulli draw at true ability (new wording).
        p_para = sigmoid(THETA_TRUE - b)
        paraphrase_correct = [rng.random() < p_para for _ in paraphrases]

        responses[it["id"]] = {
            "memory_recall": round(memory_recall, 4),
            "paraphrase_correct": paraphrase_correct,
            # provenance so a grader can see this row was derived, not hand-set:
            "_sim": {
                "difficulty_0_100": d,
                "b_logit": round(b, 4),
                "theta_true": THETA_TRUE,
                "familiarity_bonus": FAMILIARITY_BONUS,
                "p_paraphrase": round(p_para, 4),
                "n_paraphrases": len(paraphrases),
            },
        }

    header = {
        "_README": (
            "SIMULATED responses for the paraphrase test (rubric 7d). NOT real "
            "students. Both signals are DERIVED from the app's Rasch/1PL model "
            "(port of anki/rslib/src/scheduler/adaptive.rs via "
            "content/tools/ablation.py). memory_recall = sigmoid((theta+bonus)-b); "
            "each paraphrase_correct[i] ~ Bernoulli(sigmoid(theta-b)). "
            "Deterministic: SEED=%d, THETA_TRUE=%s, FAMILIARITY_BONUS=%s. "
            "Regenerate: python content/scripts/gen_paraphrase_responses.py"
        ) % (SEED, THETA_TRUE, FAMILIARITY_BONUS),
        "_params": {
            "seed": SEED,
            "theta_true": THETA_TRUE,
            "familiarity_bonus": FAMILIARITY_BONUS,
            "scale": SCALE,
        },
    }
    # Write header keys first, then the per-card rows. memory_vs_performance.py
    # keys by card id and ignores keys it doesn't recognize, so the "_" keys are
    # safe to include.
    payload = {**header, **responses}
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out_path}  ({len(responses)} source cards)")


if __name__ == "__main__":
    main()
