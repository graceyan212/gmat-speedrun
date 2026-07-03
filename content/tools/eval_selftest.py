#!/usr/bin/env python3
"""Self-test for eval_difficulty.py — proves the harness DISTINGUISHES a good
difficulty model from a lossy one *when the data has signal*.

Why this exists: on the student's real answers the eval currently ties, because
those answers had almost no wrong responses (theta ~ +3.5, everything counted
"correct") — so there is nothing for any difficulty model to explain, and both
tie near-perfectly. That is a data limitation, not a broken harness. This
self-test feeds the SAME Brier/accuracy machinery synthetic answers WITH signal
(a mid-ability student who misses hard items) and confirms it rates a
fine-grained (AI-like) difficulty BELOW the coarse 3-bucket baseline. It
validates the methodology; it says nothing about the real LLM ratings.

Run: python content/tools/eval_selftest.py   (exit 0 = harness valid)
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_difficulty import COARSE, brier_and_acc, estimate_theta, logit, sigmoid


def coarse_bucket(d: float) -> float:
    """Continuous 0-100 difficulty -> the coarse 20/50/80 baseline."""
    if d < 100 / 3:
        return COARSE["easy"]
    if d < 200 / 3:
        return COARSE["medium"]
    return COARSE["hard"]


M, K = 60, 6      # 60 questions, 6 answers each = 360 reviews per student
NOISE = 3.0       # AI difficulty is a tight (good) proxy for true difficulty
RUNS = 30         # independent simulated students, so the result isn't seed-luck


def run_one(seed: int):
    """One simulated student: returns (brier_ai, acc_ai, brier_coarse, acc_coarse, frac_correct)."""
    rng = random.Random(seed)
    theta_true = rng.uniform(-0.4, 1.0)  # varying mid-range ability
    # Each question: hidden TRUE difficulty, an AI-like proxy (true+noise), coarse bucket.
    qs = []
    for _ in range(M):
        true_d = rng.uniform(8, 92)
        ai_d = min(100.0, max(0.0, true_d + rng.gauss(0, NOISE)))
        qs.append((true_d, ai_d, coarse_bucket(true_d)))
    # Simulate answers from the TRUE difficulty; record eval points per model.
    ai_pts, co_pts = [], []
    for true_d, ai_d, co_d in qs:
        p_true = sigmoid(theta_true - logit(true_d))
        for _ in range(K):
            y = 1 if rng.random() < p_true else 0
            ai_pts.append((y, logit(ai_d)))
            co_pts.append((y, logit(co_d)))
    k = int(len(ai_pts) * 0.7)

    def score(pts):
        theta = estimate_theta([(b, y) for (y, b) in pts[:k]])
        return brier_and_acc(theta, [(b, y) for (y, b) in pts[k:]])

    b_ai, a_ai = score(ai_pts)
    b_co, a_co = score(co_pts)
    frac = sum(y for (y, _) in ai_pts) / len(ai_pts)
    return b_ai, a_ai, b_co, a_co, frac


def main() -> None:
    res = [run_one(s) for s in range(RUNS)]
    mean = lambda xs: sum(xs) / len(xs)
    b_ai = mean([r[0] for r in res])
    a_ai = mean([r[1] for r in res])
    b_co = mean([r[2] for r in res])
    a_co = mean([r[3] for r in res])
    frac = mean([r[4] for r in res])
    ai_wins = sum(1 for r in res if r[0] < r[2])  # lower Brier = better

    print(f"synthetic self-test: {RUNS} students x {M} questions x {K} answers "
          f"(~{frac * 100:.0f}% correct — real right/wrong signal, unlike the flat real data)")
    print(f"  AI-like difficulty : mean Brier {b_ai:.4f}   mean accuracy {a_ai * 100:.1f}%")
    print(f"  coarse baseline    : mean Brier {b_co:.4f}   mean accuracy {a_co * 100:.1f}%")
    print(f"  mean Brier(coarse) - Brier(AI) = {b_co - b_ai:+.4f}  (positive => AI better)")
    print(f"  AI wins (lower held-out Brier) in {ai_wins}/{RUNS} runs")

    ok = (b_ai < b_co) and (ai_wins > RUNS / 2)
    print("\nHARNESS VALID:",
          "yes — averaged over many students the eval rates the finer (AI-like) "
          "difficulty below the coarse baseline when signal is present." if ok
          else "NO — investigate.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
