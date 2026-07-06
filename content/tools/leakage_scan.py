#!/usr/bin/env python3
"""Train/test leakage scan for the GMAT item bank (rubric 7e).

Question this answers: did any TEST item, or a NEAR-COPY of one, slip into the
TRAINING data? A leaked test item would let the difficulty/eval pipeline "see"
held-out content during calibration, so this must come back CLEAN.

It complements the hygiene check already in ``eval_difficulty.py`` (§2.1 of
docs/RESULTS.md). That check is ID-based: it collapses paraphrase ids (``-pN``)
to their base id so a paraphrase can't straddle the item-disjoint split. But an
ID-only check is blind to a near-copy that was filed under an *unrelated* id.
This scanner closes that gap by comparing the actual TEXT of every train item
against every test item, independent of ids.

Scope (train x test, from content/items.json):
  * Every item carries a ``split`` field. Paraphrases inherit their parent's
    split (they carry ``parent_id`` and no split of their own).
  * TRAIN  = items/paraphrases with split == "train".
  * TEST   = items/paraphrases with split in {"test", "holdout", "gold"}
             (everything NOT used for training — the strictest read of "test").
  * The single gold-set probes in items.json["gold_set"] are also folded into
    TEST (they must never appear in training either).

Text similarity (computed on NORMALIZED text = stem + choices + answer,
lowercased, punctuation stripped, whitespace collapsed):
  * difflib.SequenceMatcher ratio  — character-level, order-sensitive.
  * token Jaccard                  — |A n B| / |A u B| over word sets, order-free.
  * We report the MAX of the two, so a match on EITHER metric trips the flag.
A pair is flagged when max(ratio, jaccard) >= THRESHOLD (default 0.85). Exact
normalized-text duplicates (max == 1.0) are reported separately.

Reported counts, because legitimate paraphrases and shared GMAT question
templates are *intentionally* similar and must not be miscounted as leakage:
  (a) INTENTIONAL same-base paraphrase pairs  — high-similarity pairs that share
      a base id (e.g. Q-PS-001 vs Q-PS-001-p2). Expected; NOT leakage.
  (b) CROSS-ITEM VERBATIM LEAKS (train x test, DIFFERENT base ids, SAME question)
      — the rubric's real target: a test item, or a copy of it, sitting in train.
      Defined as a flagged cross pair whose STEM is near-identical (>= 0.97) AND
      whose correct answer matches, OR an exact normalized-text duplicate. This
      MUST be 0.
  (c) TEMPLATE-SIBLINGS  — flagged cross pairs that share a solution *schema* but
      have DIFFERENT numbers and a different/independent answer (e.g. "n^2
      divisible by 900 -> 30" vs "n^2 divisible by 72 -> 12"). GMAT is built from
      a finite set of templates, so such siblings between two large question sets
      are unavoidable; solving one does NOT reveal the other's answer, so they are
      reported transparently but are NOT leakage.

Two normalization details keep the metric honest on a formulaic math bank:
  * The 19 Data-Sufficiency items share one of two identical answer-key templates
    ("Statement (1) ALONE is sufficient..."); that boilerplate is stripped so it
    can't manufacture ~0.9 similarity between two unrelated DS questions.
  * The verbatim/template split above uses stem-only similarity + answer match so
    a shared template can't be miscounted as a leaked item.

Exit status is non-zero only if a VERBATIM leak (or exact cross duplicate) is
found, so it can gate CI. Stdlib only (difflib) — no new deps.

Usage:
  python content/tools/leakage_scan.py                 # scan, print report
  python content/tools/leakage_scan.py --threshold 0.9 # stricter threshold
  python content/tools/leakage_scan.py --json          # machine-readable summary
  python content/tools/leakage_scan.py --top 20        # show N closest cross pairs
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ITEMS_PATH = REPO / "content" / "items.json"

TRAIN_SPLITS = {"train"}
TEST_SPLITS = {"test", "holdout", "gold"}
DEFAULT_THRESHOLD = 0.85

_WORD_RE = re.compile(r"[a-z0-9]+")

# The 19 Data-Sufficiency items share one of two IDENTICAL ~300-char answer-choice
# templates (the standard GMAT DS answer key: "Statement (1) ALONE is
# sufficient..."). That boilerplate is content-free for telling two questions
# apart, but if left in the compared text it inflates the character-ratio between
# ANY two DS items to ~0.9 regardless of the actual question — a false leakage
# signal. We drop choice lines that are this template so similarity is judged on
# the discriminating stem + question-specific choices + answer.
_DS_CHOICE_RE = re.compile(r"statement.*(alone|together).*sufficient", re.IGNORECASE)


def base_id(item_id: str) -> str:
    """Collapse a paraphrase id (Q-PS-001-p2) to its base item (Q-PS-001).

    Same convention as eval_difficulty.base_id, so an intentional paraphrase and
    its parent share a base id and are recognised as an expected pair."""
    return item_id.rsplit("-p", 1)[0]


def _norm_text(stem: str, choices, answer: str) -> str:
    """Normalize an item to comparable text: stem + choices + answer, lowercased,
    punctuation dropped, whitespace collapsed. Robust to choices being a list or a
    {label: text} dict."""
    if isinstance(choices, dict):
        choice_seq = [f"{k} {v}" for k, v in choices.items()]
    elif isinstance(choices, (list, tuple)):
        choice_seq = [str(c) for c in choices]
    else:
        choice_seq = [str(choices)] if choices else []
    # Drop the boilerplate DS answer-key template (see _DS_CHOICE_RE) so it can't
    # manufacture similarity between two unrelated Data-Sufficiency questions.
    choice_str = " ".join(c for c in choice_seq if not _DS_CHOICE_RE.search(c))
    raw = f"{stem or ''} {choice_str} {answer or ''}".lower()
    # keep only word characters; collapse everything else to single spaces so
    # "$92." and "92" or "A) 6" and "a 6" normalize the same way.
    return " ".join(_WORD_RE.findall(raw))


def _tokens(norm: str) -> frozenset[str]:
    return frozenset(norm.split())


def load_records() -> list[dict]:
    """Flatten items.json into per-question records with a split label.

    One record per top-level item AND per paraphrase (paraphrases inherit the
    parent's split). Also folds items.json["gold_set"] probes into TEST."""
    d = json.loads(ITEMS_PATH.read_text())
    items = d["items"] if isinstance(d, dict) and isinstance(d.get("items"), list) else d
    records: list[dict] = []

    def add(iid, split, stem, choices, answer):
        norm = _norm_text(stem, choices, answer)
        if not norm:
            return
        records.append({
            "id": iid,
            "base": base_id(iid),
            "split": split,
            "norm": norm,
            "tokens": _tokens(norm),
            # stem-only text (no choices/answer) — used to tell a verbatim leak
            # from a template-sibling: two items sharing a template have similar
            # full text but their *stems* still differ in the actual numbers.
            "stem_norm": _norm_text(stem, [], ""),
            "answer": str(answer or "").strip().upper(),
        })

    for it in items:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        split = it.get("split")
        add(it["id"], split, it.get("stem"), it.get("choices"), it.get("answer"))
        for p in it.get("paraphrases", []) or []:
            if isinstance(p, dict) and p.get("id"):
                # paraphrase inherits the parent item's split
                add(p["id"], split, p.get("stem"), p.get("choices"), p.get("answer"))

    # gold_set probes (different shape: 'question'/'answer', no choices) -> TEST.
    for g in (d.get("gold_set") or []) if isinstance(d, dict) else []:
        if isinstance(g, dict) and g.get("id"):
            add(g["id"], "gold", g.get("question"), g.get("choices"), g.get("answer"))

    return records


def similarity(a: dict, b: dict) -> tuple[float, float]:
    """Return (SequenceMatcher ratio, token Jaccard) for two records."""
    ratio = SequenceMatcher(None, a["norm"], b["norm"]).ratio()
    ta, tb = a["tokens"], b["tokens"]
    union = ta | tb
    jacc = (len(ta & tb) / len(union)) if union else 0.0
    return ratio, jacc


# A flagged cross pair is a VERBATIM LEAK (the rubric's real target: the same
# test item, or a copy of it, sitting in train) only when the two items are
# essentially the SAME question — same correct answer AND a near-identical stem.
# Below that, a high full-text score means the two share a question *template*
# (same solution schema, DIFFERENT numbers and answer) — a "template-sibling".
# GMAT items are built from a finite set of templates, so template-siblings
# between any two large question sets are unavoidable and are NOT leakage:
# knowing the train item's answer does not give you the test item's answer.
VERBATIM_STEM_SIM = 0.97   # stem-only similarity for "same question"


def classify_cross(t: dict, s: dict, ratio: float, jacc: float) -> str:
    """'verbatim' if t and s are essentially the same question (leak), else
    'template' (same schema, different numbers/answer — reported, not leakage)."""
    mx = max(ratio, jacc)
    if mx >= 1.0 - 1e-9:
        return "verbatim"                    # exact normalized-text duplicate
    stem_sim = SequenceMatcher(None, t["stem_norm"], s["stem_norm"]).ratio()
    if stem_sim >= VERBATIM_STEM_SIM and t["answer"] == s["answer"]:
        return "verbatim"                    # same stem numbers AND same answer
    return "template"


def scan(records: list[dict], threshold: float):
    train = [r for r in records if r["split"] in TRAIN_SPLITS]
    test = [r for r in records if r["split"] in TEST_SPLITS]

    cross_verbatim = []    # (b) SAME item across train x test — the leak signal; MUST be 0
    cross_template = []    # same template, different numbers/answer — reported, not leakage
    cross_exact = []       # exact normalized-text duplicates across train x test
    intentional = []       # (a) high-sim pairs sharing a base id (paraphrases)

    for t in train:
        for s in test:
            ratio, jacc = similarity(t, s)
            mx = max(ratio, jacc)
            if mx < threshold:
                continue
            pair = {
                "train_id": t["id"], "test_id": s["id"],
                "train_ans": t["answer"], "test_ans": s["answer"],
                "ratio": round(ratio, 4), "jaccard": round(jacc, 4),
                "max": round(mx, 4),
            }
            if t["base"] == s["base"]:
                intentional.append(pair)          # expected paraphrase overlap
            elif classify_cross(t, s, ratio, jacc) == "verbatim":
                cross_verbatim.append(pair)        # LEAK
                if mx >= 1.0 - 1e-9:
                    cross_exact.append(pair)
            else:
                cross_template.append(pair)        # template-sibling (not leakage)

    # Also count intentional paraphrase similarity WITHIN each split (not just
    # across the train/test boundary) for a full picture of expected overlap.
    same_base_all = 0
    n = len(records)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            if a["base"] != b["base"]:
                continue
            ratio, jacc = similarity(a, b)
            if max(ratio, jacc) >= threshold:
                same_base_all += 1

    cross_verbatim.sort(key=lambda p: p["max"], reverse=True)
    cross_template.sort(key=lambda p: p["max"], reverse=True)
    return {
        "threshold": threshold,
        "n_train": len(train),
        "n_test": len(test),
        "n_pairs_compared": len(train) * len(test),
        "intentional_same_base_cross_pairs": len(intentional),
        "intentional_same_base_pairs_total": same_base_all,
        # THE leakage number: same test item (or a verbatim copy) in train.
        "cross_item_verbatim_leaks": len(cross_verbatim),
        "cross_item_exact_duplicates": len(cross_exact),
        # template-siblings: high full-text similarity but different numbers/answer.
        "cross_item_template_siblings": len(cross_template),
        "verbatim_flags": cross_verbatim,
        "template_flags": cross_template,
        "intentional_examples": intentional[:5],
    }


def top_cross_pairs(records: list[dict], k: int):
    """Highest-similarity train x test pairs with DIFFERENT base ids, regardless
    of threshold — the honest 'how close did anything get?' view."""
    train = [r for r in records if r["split"] in TRAIN_SPLITS]
    test = [r for r in records if r["split"] in TEST_SPLITS]
    best = []
    for t in train:
        for s in test:
            if t["base"] == s["base"]:
                continue
            ratio, jacc = similarity(t, s)
            best.append((max(ratio, jacc), ratio, jacc, t["id"], s["id"]))
    best.sort(reverse=True)
    return best[:k]


def main() -> int:
    ap = argparse.ArgumentParser(description="train/test near-copy leakage scan")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"flag pairs with max(ratio,jaccard) >= this (default {DEFAULT_THRESHOLD})")
    ap.add_argument("--json", action="store_true", help="print machine-readable summary")
    ap.add_argument("--top", type=int, default=5,
                    help="show N closest cross-item pairs (default 5)")
    args = ap.parse_args()

    records = load_records()
    result = scan(records, args.threshold)

    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result["cross_item_verbatim_leaks"] else 0

    print("=== Train/test leakage scan (rubric 7e) ===")
    print(f"source: {ITEMS_PATH.relative_to(REPO)}")
    print(f"scope : TRAIN split={sorted(TRAIN_SPLITS)}  vs  "
          f"TEST split={sorted(TEST_SPLITS)}")
    print(f"metric: max(difflib SequenceMatcher ratio, token Jaccard) on "
          f"normalized stem+choices+answer")
    print(f"threshold: {result['threshold']}   verbatim stem-sim: {VERBATIM_STEM_SIM}\n")

    print(f"train records (items+paraphrases): {result['n_train']}")
    print(f"test  records (items+paraphrases): {result['n_test']}")
    print(f"train x test pairs compared      : {result['n_pairs_compared']}\n")

    print("(a) INTENTIONAL same-base paraphrase pairs (expected, NOT leakage):")
    print(f"      across train x test boundary : {result['intentional_same_base_cross_pairs']}")
    print(f"      total anywhere in the bank   : {result['intentional_same_base_pairs_total']}")
    for ex in result["intentional_examples"]:
        print(f"        - {ex['train_id']} ~ {ex['test_id']}  "
              f"(ratio={ex['ratio']}, jaccard={ex['jaccard']}, max={ex['max']})")

    print("\n(b) CROSS-ITEM VERBATIM LEAKS (train x test, DIFFERENT base id,")
    print("    same question: near-identical stem AND same answer) — MUST be 0:")
    print(f"      verbatim leaks (>= threshold): {result['cross_item_verbatim_leaks']}")
    print(f"      exact normalized duplicates  : {result['cross_item_exact_duplicates']}")
    if result["verbatim_flags"]:
        print("      LEAKED PAIRS:")
        for p in result["verbatim_flags"]:
            print(f"        - TRAIN {p['train_id']} (ans {p['train_ans']})  <->  "
                  f"TEST {p['test_id']} (ans {p['test_ans']})  "
                  f"(ratio={p['ratio']}, jaccard={p['jaccard']}, max={p['max']})")

    print("\n(c) TEMPLATE-SIBLINGS above threshold (same solution schema, DIFFERENT")
    print("    numbers and answer — reported for transparency, NOT leakage):")
    print(f"      count: {result['cross_item_template_siblings']}")
    for p in result["template_flags"]:
        note = "different answer" if p["train_ans"] != p["test_ans"] else "same answer, stem numbers differ"
        print(f"        - TRAIN {p['train_id']} (ans {p['train_ans']})  ~  "
              f"TEST {p['test_id']} (ans {p['test_ans']})  "
              f"(max={p['max']}; {note})")

    closest = top_cross_pairs(records, args.top)
    print(f"\nclosest {len(closest)} cross-item pairs overall (context — most are template-siblings):")
    for mx, ratio, jacc, tid, sid in closest:
        print(f"      {mx:.3f}  TRAIN {tid} <-> TEST {sid}  (ratio={ratio:.3f}, jaccard={jacc:.3f})")

    clean = result["cross_item_verbatim_leaks"] == 0
    print()
    if clean:
        print("RESULT: CLEAN — 0 verbatim test items (or copies) leaked into train.")
        if result["cross_item_template_siblings"]:
            print(f"        ({result['cross_item_template_siblings']} template-sibling(s) noted above "
                  "share a solution schema but differ in numbers/answer — not leakage.)")
        return 0
    print(f"RESULT: LEAKAGE — {result['cross_item_verbatim_leaks']} verbatim leak(s) found (see above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
