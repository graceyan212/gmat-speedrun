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

Leakage check (train/test hygiene) — printed after the headline result:
  1. Provenance: the item difficulties are CONTENT-ONLY. `calibrate_difficulty.py`
     rates each item from its text + the rubric with no access to the revlog, so
     the predictor `b` cannot encode the held-out labels (no target leakage).
  2. The default time-split shares items across train/held (a card reviewed in
     both halves). We report that overlap for transparency.
  3. Item-disjoint split: re-run with train/held partitioned BY ITEM (paraphrases
     grouped to their base id) so the two sets share NO item — this removes any
     item-level leakage. If AI still matches/beats coarse here, the comparison
     isn't an artifact of shared items.

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


# Points are 4-tuples: (item_id, correct, ai_logit, coarse_logit).

def base_id(item_id: str) -> str:
    """Collapse a paraphrase id (Q-PS-001-p2) to its base item (Q-PS-001) so
    near-duplicate paraphrases can't straddle the item-disjoint split."""
    return item_id.rsplit("-p", 1)[0]


def fit_and_score(train, held):
    """Fit theta on train (once per difficulty source) and score held-out."""
    theta_ai = estimate_theta([(ai, y) for (_i, y, ai, _c) in train])
    theta_co = estimate_theta([(co, y) for (_i, y, _a, co) in train])
    b_ai, a_ai = brier_and_acc(theta_ai, [(ai, y) for (_i, y, ai, _c) in held])
    b_co, a_co = brier_and_acc(theta_co, [(co, y) for (_i, y, _a, co) in held])
    return (theta_ai, b_ai, a_ai), (theta_co, b_co, a_co)


def print_result(ai, co) -> float | None:
    theta_ai, b_ai, a_ai = ai
    theta_co, b_co, a_co = co
    print("            theta   held-out Brier   held-out accuracy")
    print(f"  AI diff   {theta_ai:+.2f}   {b_ai:.4f}          {a_ai * 100:.0f}%")
    print(f"  coarse    {theta_co:+.2f}   {b_co:.4f}          {a_co * 100:.0f}%")
    if b_ai is None or b_co is None:
        return None
    delta = b_co - b_ai  # positive => AI is better (lower Brier)
    verdict = ("AI difficulty beats the coarse baseline" if delta > 1e-3
               else "coarse baseline is as good or better" if delta < -1e-3
               else "tie")
    print(f"  Brier(coarse) - Brier(AI) = {delta:+.4f}  ->  {verdict}")
    return delta


def item_disjoint_split(pts, frac):
    """Partition BY base item so train and held-out share no item. Items ordered
    by first appearance (time). Returns (train, held, n_train_items, n_held_items)."""
    order, seen = [], set()
    for (iid, *_rest) in pts:
        b = base_id(iid)
        if b not in seen:
            seen.add(b)
            order.append(b)
    k = max(1, int(len(order) * frac))
    train_items = set(order[:k])
    train = [p for p in pts if base_id(p[0]) in train_items]
    held = [p for p in pts if base_id(p[0]) not in train_items]
    return train, held, len(train_items), len(order) - len(train_items)


def leakage_check(pts, train, held, frac) -> None:
    """Explicit train/test-hygiene check for the difficulty eval (see module docstring)."""
    print("\n--- Leakage check (train/test hygiene) ---")
    print("  1. content-only difficulty: AI ratings come from the item text +")
    print("     rubric (calibrate_difficulty.py), computed with NO access to the")
    print("     revlog, so `b` can't encode held-out outcomes (no target leakage).")

    train_items = {base_id(p[0]) for p in train}
    held_items = {base_id(p[0]) for p in held}
    shared = train_items & held_items
    print(f"  2. time-split item overlap: {len(shared)} of {len(held_items)} held-out")
    print("     items were also seen in train (the 1-parameter Rasch has no")
    print("     per-item weight, but the disjoint check below removes it anyway).")

    dj_train, dj_held, n_tr, n_he = item_disjoint_split(pts, frac)
    print(f"  3. item-disjoint split: {n_tr} train items ({len(dj_train)} reviews),")
    print(f"     {n_he} held items ({len(dj_held)} reviews) — ZERO shared items.")
    if len(dj_train) < 4 or len(dj_held) < 4:
        print("     (too few reviews on disjoint items to score — need more study data.)")
        return
    dj_ai, dj_co = fit_and_score(dj_train, dj_held)
    print_result(dj_ai, dj_co)
    if len(dj_held) < 12:
        print("     CAVEAT: disjoint held-out is small; directional, not conclusive.")


def main() -> None:
    ap = argparse.ArgumentParser(description="AI-difficulty vs coarse-tag eval")
    ap.add_argument("collection", help="path to a collection.anki2")
    ap.add_argument("--split", type=float, default=0.7, help="train fraction (by time)")
    args = ap.parse_args()

    ai = json.loads(SIDECAR.read_text())
    rows = load_reviews(args.collection)

    # Build aligned (item_id, correct, ai_b, coarse_b) points for reviews with BOTH.
    pts = []
    skipped_no_id = skipped_no_ai = skipped_no_coarse = 0
    for _rid, ease, tags in rows:
        item_id, coarse = parse_tags(tags)
        y = 1 if ease >= 2 else 0
        if not item_id:
            skipped_no_id += 1
            continue
        entry = ai.get(item_id) or ai.get(base_id(item_id))
        if not entry:
            skipped_no_ai += 1
            continue
        if coarse is None:
            skipped_no_coarse += 1
            continue
        pts.append((item_id, y, logit(float(entry["ai_difficulty"])), logit(coarse)))

    n = len(pts)
    print(f"reviews in log: {len(rows)} | usable (have id::+ai+coarse): {n}")
    print(f"  skipped: no id:: tag={skipped_no_id}, no AI rating={skipped_no_ai}, "
          f"no coarse tag={skipped_no_coarse}")
    if n < 4:
        print("\nNot enough usable reviews to split — answer more questions, then re-run.")
        return

    k = max(1, int(n * args.split))
    train, held = pts[:k], pts[k:]
    print(f"  train={len(train)}  held-out={len(held)}  (split={args.split}, by time)")

    print("\n[time-split held-out]")
    ai_res, co_res = fit_and_score(train, held)
    print_result(ai_res, co_res)
    if len(held) < 12:
        print("  CAVEAT: held-out set is small; treat this as directional, not conclusive.")

    leakage_check(pts, train, held, args.split)


if __name__ == "__main__":
    main()
