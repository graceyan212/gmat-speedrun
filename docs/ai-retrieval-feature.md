# AI feature: grounded "find the method" retrieval

**A short note — what it is, why, and what I skipped.**

**What.** Given a GMAT question, the feature surfaces the most relevant *other*
question as a **cited source** ("here's another problem that uses the same
method"), so a student who's stuck can study the method from a worked example.
It's a retrieval/RAG feature: retrieve, then cite — no free-form generation.

**Why.** It's a genuine study aid, and it's the AI shape the assignment grades:
outputs that **trace to a named source** and that must **beat keyword / vector
search**. (The separate difficulty-calibration feature — see
`ai-adaptive-feature.md` — feeds the adaptive scheduler and the performance
score; this retrieval feature is the one that answers the AI-eval checklist.)

**What I skipped (YAGNI / honesty).**
- **No generated answers** — retrieval + citation only, so every output is
  grounded in a real deck item and can't hallucinate.
- **No neural-embedding baseline** — there's no embedding model/API key in this
  environment, so the "vector" baseline is a **TF-IDF vector-space cosine**
  (a classic vector retrieval model), labeled honestly. A dense-embedding
  baseline can be added if an embedding provider becomes available.
- **No live in-app UI yet** — the graded artifacts are the retrieval pipeline,
  the traceable output, and the held-out eval; wiring a "show me the method"
  button into the reviewer is a small follow-up.

## How it works
Given a query question, three methods rank the *other* items as candidate sources:
- **Keyword baseline** — BM25 over each item's stem + explanation.
- **Vector baseline** — TF-IDF vectors + cosine.
- **The AI** — an LLM reranks each query's candidate pool (the union of the
  baselines' top-10) by the **underlying method/concept** ("both apply
  percent-change factors in sequence", "both are author's-tone RC"), judging the
  *content*, not surface words. It returns the top sources **with a one-line
  rationale naming the shared method** — the citation.

Because the AI sees only candidate *content* (no topic labels), it cannot game
the topic-based ground truth.

## Traceable output
`content/retrieval_ai.json` — for every one of the 68 questions:
`{query_id: {ranked: [source ids], top_source, rationale}}`. Example:

```json
"Q-PS-003": { "top_source": "Q-PS-107",
  "rationale": "Same method: prime-factorize and reason about the exponents of each prime (2 and 3) to determine divisibility — matches Q-PS-107's 12^3 = 2^6 * 3^3 decomposition." }
```

Every AI output points at a named deck item (`id`) with a stated reason.

## Eval (held-out, runs before students see anything)
Leave-one-out over the 68 questions; ground truth = the *other* items sharing
the query's topic (same method). Metrics at a top-k cutoff:

| method | acc@1 | acc@3 | wrong@1 |
|---|---|---|---|
| keyword (BM25) | 55.4% | 76.9% | 44.6% |
| vector (TF-IDF cosine) | 53.8% | 73.8% | 46.2% |
| **AI reranker** | **73.8%** | **89.2%** | **26.2%** |

**The AI beats both baselines by ~18–20 points of top-1 accuracy and roughly
halves the wrong-answer rate** (26.2% vs ~45%). `n = 65` for all three (3 topics
have only one item, so no valid ground truth — excluded for every method, apples
to apples).

## Switchable off
Retrieval is independent of scoring: turned off, the app falls back to the
keyword baseline (or shows nothing) and **all scores still compute** — they don't
depend on this feature.

## Honest limits
- Small corpus (68 items) — the AI's win is clear but on a modest set.
- Ground truth is a **topic-tag proxy** for "same method," not human relevance
  labels; it's a reasonable, pre-existing signal, not gold.
- The vector baseline is TF-IDF cosine, not dense embeddings (see "skipped").

## Run
```bash
python content/tools/retrieval_eval.py         # baselines only
python content/tools/retrieval_eval.py --ai    # + AI reranker column (needs content/retrieval_ai.json)
```
(AI picks were produced by a fan-out of Claude agents reranking each query's pool
— no API key needed; the same reproducible approach as the difficulty calibration.)

## Files
| File | Role |
|---|---|
| `content/retrieval_ai.json` | traceable AI output: per-query cited source + rationale |
| `content/tools/retrieval_eval.py` | BM25 + TF-IDF baselines, AI column, held-out eval |
