#!/usr/bin/env python3
"""Grounded-retrieval AI feature + held-out eval vs keyword and vector baselines.

The app's AI feature (checklist: "traces to a named source" + "beats keyword or
vector search"): given a GMAT question, retrieve the most relevant OTHER item's
explanation as a cited source (a study aid — "here's the method, and here's where
it's explained"). Every result cites a named source (`id`) + a rationale.

This module implements the two baselines and the held-out eval. The AI reranker
(Claude, no API key needed — same fan-out trick as difficulty calibration) is run
separately and its top-1 picks are read from `content/retrieval_ai.json`; if that
file is absent, only the baselines are scored.

Ground truth: for a query item, the "relevant" sources are the OTHER items sharing
its `topic` (same method). Metrics on a held-out query set:
  * accuracy@1 / accuracy@3  — a correct-topic source in the top-1 / top-3
  * wrong@1                  — top-1 is off-topic (1 - accuracy@1)
  * abstain cutoff           — if the top score is below a threshold, retrieve
                               nothing rather than a bad source (honest cutoff).

Run: python content/tools/retrieval_eval.py            # baselines
     python content/tools/retrieval_eval.py --ai       # include AI column if retrieval_ai.json exists
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ITEMS = REPO / "content" / "items.json"
AI_PICKS = REPO / "content" / "retrieval_ai.json"  # {query_id: source_id} from the AI reranker

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def load_items() -> list[dict]:
    d = json.loads(ITEMS.read_text())
    its = d if isinstance(d, list) else (d["items"] if isinstance(d.get("items"), list) else list(d.values()))
    return [x for x in its if isinstance(x, dict) and x.get("id")]


def doc_text(it: dict) -> str:
    """Retrieval text for an item: stem + explanation (+ paraphrases if present)."""
    parts = [str(it.get("stem", "")), str(it.get("explanation", ""))]
    pp = it.get("paraphrases")
    if isinstance(pp, list):
        parts += [str(p) for p in pp]
    return " ".join(parts)


# ---- baselines ------------------------------------------------------------

class Corpus:
    def __init__(self, items: list[dict]):
        self.items = items
        self.ids = [it["id"] for it in items]
        self.topic = {it["id"]: it.get("topic") for it in items}
        self.toks = {it["id"]: tokenize(doc_text(it)) for it in items}
        # df / idf over the corpus
        df: Counter = Counter()
        for tid in self.ids:
            df.update(set(self.toks[tid]))
        n = len(self.ids)
        self.idf = {t: math.log((n - c + 0.5) / (c + 0.5) + 1.0) for t, c in df.items()}
        self.avg_len = sum(len(self.toks[t]) for t in self.ids) / max(1, n)
        # tf-idf vectors (for the vector baseline)
        self.vec = {tid: self._tfidf_vec(self.toks[tid]) for tid in self.ids}
        self.vnorm = {tid: math.sqrt(sum(w * w for w in v.values())) or 1.0 for tid, v in self.vec.items()}

    def _tfidf_vec(self, toks: list[str]) -> dict[str, float]:
        tf = Counter(toks)
        return {t: (1 + math.log(c)) * self.idf.get(t, 0.0) for t, c in tf.items()}

    def bm25(self, q_toks: list[str], src_id: str, k1: float = 1.5, b: float = 0.75) -> float:
        tf = Counter(self.toks[src_id])
        dl = len(self.toks[src_id])
        score = 0.0
        for t in set(q_toks):
            if t not in tf:
                continue
            idf = self.idf.get(t, 0.0)
            score += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * dl / self.avg_len))
        return score

    def cosine(self, q_id: str, src_id: str) -> float:
        qv, sv = self.vec[q_id], self.vec[src_id]
        # dot over the smaller vector
        small, big = (qv, sv) if len(qv) < len(sv) else (sv, qv)
        dot = sum(w * big.get(t, 0.0) for t, w in small.items())
        return dot / (self.vnorm[q_id] * self.vnorm[src_id])

    def rank(self, method: str, q_id: str) -> list[tuple[str, float]]:
        q_toks = self.toks[q_id]
        scored = []
        for sid in self.ids:
            if sid == q_id:
                continue
            s = self.bm25(q_toks, sid) if method == "keyword" else self.cosine(q_id, sid)
            scored.append((sid, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ---- eval -----------------------------------------------------------------

def eval_method(corpus: Corpus, ranker, queries: list[str]) -> dict:
    """ranker(q_id) -> ordered list of source ids. Returns accuracy@1/@3, wrong@1."""
    hit1 = hit3 = 0
    n = 0
    for q in queries:
        gt_topic = corpus.topic[q]
        # only score queries whose topic has at least one OTHER item (else unanswerable)
        others = [s for s in corpus.ids if s != q and corpus.topic[s] == gt_topic]
        if not others:
            continue
        n += 1
        ranked = ranker(q)
        top3 = ranked[:3]
        if top3 and corpus.topic[top3[0]] == gt_topic:
            hit1 += 1
        if any(corpus.topic[s] == gt_topic for s in top3):
            hit3 += 1
    return {
        "n": n,
        "acc@1": hit1 / n if n else 0.0,
        "acc@3": hit3 / n if n else 0.0,
        "wrong@1": 1 - hit1 / n if n else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true", help="include the AI reranker column (needs retrieval_ai.json)")
    args = ap.parse_args()

    items = load_items()
    corpus = Corpus(items)
    queries = corpus.ids  # every item is a held-out query in turn (leave-one-out)

    methods = {
        "keyword (BM25)": lambda q: [s for s, _ in corpus.rank("keyword", q)],
        "vector (TF-IDF cosine)": lambda q: [s for s, _ in corpus.rank("vector", q)],
    }
    if args.ai and AI_PICKS.exists():
        picks = json.loads(AI_PICKS.read_text())  # {query_id: {ranked:[ids], rationale}} or a bare list/id
        def ai_ranker(q):
            p = picks.get(q)
            if isinstance(p, dict):
                return p.get("ranked", [])
            if isinstance(p, list):
                return p
            return [p] if p else []
        methods["AI reranker"] = ai_ranker

    print(f"Grounded-retrieval eval — leave-one-out over {len(queries)} questions")
    print(f"ground truth = shares the query's topic; {len(set(corpus.topic.values()))} topics\n")
    print(f"{'method':26} {'n':>4} {'acc@1':>7} {'acc@3':>7} {'wrong@1':>8}")
    for name, ranker in methods.items():
        r = eval_method(corpus, ranker, queries)
        print(f"{name:26} {r['n']:>4} {r['acc@1']*100:>6.1f}% {r['acc@3']*100:>6.1f}% {r['wrong@1']*100:>7.1f}%")
    if not (args.ai and AI_PICKS.exists()):
        print("\n(AI column omitted: run the reranker to produce content/retrieval_ai.json, then --ai)")


if __name__ == "__main__":
    main()
