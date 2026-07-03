#!/usr/bin/env python3
"""Memory-vs-Performance experiment: does the performance signal differ from the
memory signal, or is it just copying it?

For each source card that has >=2 paraphrases (same idea, new words):
  memory_recall = P(recall the EXACT card)      -- from FSRS retrievability / pass-rate
  perf_accuracy = fraction correct on that card's PARAPHRASES
  gap           = perf_accuracy - memory_recall

If the average gap is ~0 (and the two track each other), the performance model is
just mirroring the memory model -- no bridge. A negative gap (paraphrases harder
than raw recall) is the signal that memorizing a card does NOT equal understanding.

Usage:  memory_vs_performance.py [responses.json]
  responses.json: { "<card_id>": {"memory_recall": 0.0-1.0,
                                   "paraphrase_correct": [true,false,...]}, ... }
Without a responses file, prints the experiment set and the data it needs.
"""
import json
import sys

ITEMS = "content/items.json"


def experiment_set():
    data = json.load(open(ITEMS))
    items = data if isinstance(data, list) else data["items"]
    cards = {}
    for it in items:
        ps = it.get("paraphrases") or []
        if len(ps) >= 2:
            cards[it["id"]] = [p["id"] for p in ps]
    return cards


def main():
    cards = experiment_set()
    if len(sys.argv) < 2:
        print(f"Experiment set: {len(cards)} source cards each have >=2 linked paraphrases.")
        print("Provide responses.json to compute the gap. Expected per card:")
        print('  { "'+ (next(iter(cards)) if cards else "Q-PS-101") +'": {"memory_recall": 0.9, "paraphrase_correct": [true,false]} }')
        print("memory_recall comes from the card's FSRS retrievability / pass-rate;")
        print("paraphrase_correct comes from the student answering the linked paraphrases.")
        return

    resp = json.load(open(sys.argv[1]))
    rows, gaps = [], []
    for cid in cards:
        r = resp.get(cid)
        if not r or not r.get("paraphrase_correct"):
            continue
        mem = float(r["memory_recall"])
        pc = r["paraphrase_correct"]
        perf = sum(1 for x in pc if x) / len(pc)
        gap = perf - mem
        rows.append((cid, mem, perf, gap))
        gaps.append(gap)

    if not rows:
        print("No overlapping response data for the experiment cards yet.")
        return

    n = len(rows)
    mean_mem = sum(r[1] for r in rows) / n
    mean_perf = sum(r[2] for r in rows) / n
    mean_gap = sum(gaps) / n
    mean_abs = sum(abs(g) for g in gaps) / n

    print(f"cards scored: {n}")
    print(f"mean memory recall     : {mean_mem:.3f}")
    print(f"mean paraphrase accuracy: {mean_perf:.3f}")
    print(f"mean gap (perf - memory): {mean_gap:+.3f}   mean |gap|: {mean_abs:.3f}")
    print("-" * 52)
    if mean_abs < 0.05:
        print("VERDICT: gap ~ 0 -> performance is mirroring memory. Bridge NOT built.")
    elif mean_gap < -0.05:
        print("VERDICT: paraphrases are harder than raw recall -> performance captures")
        print("         understanding beyond memorization. Bridge exists.")
    else:
        print("VERDICT: performance and memory diverge; inspect per-card rows below.")
    print("-" * 52)
    for cid, mem, perf, gap in sorted(rows, key=lambda r: r[3]):
        print(f"  {cid:14s} memory={mem:.2f} perf={perf:.2f} gap={gap:+.2f}")


if __name__ == "__main__":
    main()
