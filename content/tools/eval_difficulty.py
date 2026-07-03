#!/usr/bin/env python3
"""Eval: does AI-rated difficulty predict the student's answers better than the
coarse difficulty tags?

This is the "beats a simple baseline" test for the AI difficulty feature
(calibrate_difficulty.py). Baseline = the coarse `difficulty::easy|medium|hard`
tags (mapped to 20/50/80). Challenger = the fine-grained `ai_difficulty` 0-100
from content/ai_difficulty.json.

Method (held-out, honest):
  * Pull the student's real reviews from a collection's revlog (ease>=2 = correct;
    exclude ease==0 and manual reschedules type==4).
  * Join each review to its item via the note's `id::<item_id>` tag -> AI difficulty
    (sidecar) and coarse difficulty (the note's `difficulty::` tag).
  * Fit a 1-parameter Rasch ability theta on the first 70% of reviews (by time),
    once using AI difficulty and once using coarse difficulty.
  * On the held-out last 30%, predict P(correct)=sigmoid(theta - b) and score:
      - Brier score (mean squared error of the probability; LOWER = better calibrated)
      - accuracy (threshold 0.5)
  * AI "beats baseline" iff its held-out Brier is meaningfully lower than coarse's.

The eval reports honestly even when the data is too thin to be conclusive.

Usage:
  python content/tools/eval_difficulty.py /path/to/collection.anki2
  python content/tools/eval_difficulty.py /path/to/collection.anki2 --split 0.7
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIDECAR = REPO / "content" / "ai_difficulty.json"
SCALE = 4.0
COARSE = {"easy": 20.0, "medium": 50.0, "hard": 80.0}


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def logit(diff_0_100: float) -> float:
    return (diff_0_100 / 100.0 - 0.5) * SCALE


def estimate_theta(obs: list[tuple[float, int]]) -> float:
    """Newton MLE for Rasch ability from (b_logit, correct) observations."""
    if not obs:
        return 0.0
    theta = 0.0
    for _ in range(50):
        g = 0.0  # gradient of log-likelihood
        h = 0.0  # (negative) second derivative
        for b, y in obs:
            p = sigmoid(theta - b)
            g += y - p
            h += p * (1.0 - p)
        if h < 1e-9:
            break
        step = g / h
        theta += step
        theta = max(-4.0, min(4.0, theta))
        if abs(step) < 1e-6:
            break
    return theta


def parse_tags(tags: str) -> tuple[str | None, float | None]:
    """Return (item_id from id::, coarse difficulty 0-100 from difficulty::)."""
    item_id = None
    coarse = None
    for t in tags.split():
        low = t.lower()
        if low.startswith("id::"):
            item_id = t.split("::", 1)[1]
        elif low.startswith("difficulty::"):
            val = t.split("::", 1)[1].lower()
            if val in COARSE:
                coarse = COARSE[val]
            else:
                try:
                    coarse = max(0.0, min(100.0, float(val)))
                except ValueError:
                    pass
    return item_id, coarse


def load_reviews(col_path: str):
    con = sqlite3.connect(col_path)
    rows = con.execute(
        """
        SELECT r.id, r.ease, n.tags
        FROM revlog r
        JOIN cards c ON r.cid = c.id
        JOIN notes n ON c.nid = n.id
        WHERE r.ease > 0 AND r.type != 4
        ORDER BY r.id
        """
    ).fetchall()
    con.close()
    return rows


def brier_and_acc(theta: float, held: list[tuple[float, int]]):
    if not held:
        return None, None
    bs = 0.0
    correct = 0
    for b, y in held:
        p = sigmoid(theta - b)
        bs += (p - y) ** 2
        if (p >= 0.5) == bool(y):
            correct += 1
    return bs / len(held), correct / len(held)


def main() -> None:
    ap = argparse.ArgumentParser(description="AI-difficulty vs coarse-tag eval")
    ap.add_argument("collection", help="path to a collection.anki2")
    ap.add_argument("--split", type=float, default=0.7, help="train fraction (by time)")
    args = ap.parse_args()

    ai = json.loads(SIDECAR.read_text())
    rows = load_reviews(args.collection)

    # Build aligned (correct, ai_b, coarse_b) points for reviews that have BOTH.
    pts = []  # (correct, ai_logit, coarse_logit)
    skipped_no_id = skipped_no_ai = skipped_no_coarse = 0
    for _rid, ease, tags in rows:
        item_id, coarse = parse_tags(tags)
        y = 1 if ease >= 2 else 0
        if not item_id:
            skipped_no_id += 1
            continue
        entry = ai.get(item_id) or ai.get(item_id.rsplit("-p", 1)[0])
        if not entry:
            skipped_no_ai += 1
            continue
        if coarse is None:
            skipped_no_coarse += 1
            continue
        pts.append((y, logit(float(entry["ai_difficulty"])), logit(coarse)))

    n = len(pts)
    print(f"reviews in log: {len(rows)} | usable (have id::+ai+coarse): {n}")
    print(f"  skipped: no id:: tag={skipped_no_id}, no AI rating={skipped_no_ai}, "
          f"no coarse tag={skipped_no_coarse}")
    if n < 4:
        print("\nNot enough usable reviews to split — answer more questions, then re-run.")
        return

    k = max(1, int(n * args.split))
    train, held = pts[:k], pts[k:]
    print(f"  train={len(train)}  held-out={len(held)}  (split={args.split})")

    theta_ai = estimate_theta([(b, y) for (y, b, _) in train])
    theta_co = estimate_theta([(b, y) for (y, _, b) in train])

    b_ai, a_ai = brier_and_acc(theta_ai, [(b, y) for (y, b, _) in held])
    b_co, a_co = brier_and_acc(theta_co, [(b, y) for (y, _, b) in held])

    print("\n            theta   held-out Brier   held-out accuracy")
    print(f"  AI diff   {theta_ai:+.2f}   {b_ai:.4f}          {a_ai*100:.0f}%")
    print(f"  coarse    {theta_co:+.2f}   {b_co:.4f}          {a_co*100:.0f}%")

    if b_ai is None or b_co is None:
        return
    delta = b_co - b_ai  # positive => AI is better (lower Brier)
    verdict = ("AI difficulty beats the coarse baseline" if delta > 1e-3
               else "coarse baseline is as good or better" if delta < -1e-3
               else "tie")
    print(f"\n  Brier(coarse) - Brier(AI) = {delta:+.4f}  ->  {verdict}")
    if len(held) < 12:
        print("  CAVEAT: held-out set is small; treat this as directional, not conclusive.")


if __name__ == "__main__":
    main()
