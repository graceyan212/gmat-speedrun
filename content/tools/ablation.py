#!/usr/bin/env python3
"""Ablation SIMULATION of the GMAT-on-Anki adaptive study feature.

This is explicitly a SIMULATION, not a human study. A true evaluation would run
three parallel builds of the app against real learners for an equal amount of
study time and compare score gains. That was not feasible solo, so this is a
Monte-Carlo proxy: a simulated learner with a fixed TRUE ability answers items
under three item-selection policies, holding the study budget equal (same number
of item-attempts N per arm, same seeds), and we measure how well each policy (a)
keeps items in a desirable-difficulty band and (b) recovers the learner's ability.

The three arms mirror the real code paths in
  anki/rslib/src/scheduler/adaptive.rs  (Rasch/1PL ability estimate + fit distance)
  anki/rslib/src/scheduler/queue/builder/sorting.rs  (points-at-stake reorder)

  (1) ADAPTIVE : pick the unseen item whose difficulty b is nearest the learner's
                 CURRENT estimated ability theta_hat (the adaptive.rs fit_distance
                 tie-break), re-estimating theta_hat after every answer.
  (2) OFF      : the app's DEFAULT non-adaptive order = points-at-stake,
                 weakest-topic first (sort_review in sorting.rs). theta is still
                 estimated for the metric, but it does NOT steer selection.
  (3) PLAIN    : plain-Anki proxy = random / DB order, no weighting at all.

The Rasch math below is a line-for-line port of adaptive.rs so the simulation
uses the SAME estimator the shipped app uses.
"""

import json
import math
import os
import random
import statistics
from pathlib import Path

# ---------------------------------------------------------------------------
# Rasch / 1PL primitives -- ported verbatim from anki/rslib/src/scheduler/adaptive.rs
# ---------------------------------------------------------------------------

SCALE = 4.0          # adaptive.rs: const SCALE (0-100 difficulty -> logit)
THETA_BOUND = 4.0    # adaptive.rs: const THETA_BOUND (theta clamp)
NEWTON_ITERS = 10    # adaptive.rs: const NEWTON_ITERS

# adaptive.rs coarse difficulty::easy|medium|hard mapping
COARSE = {"easy": 20.0, "medium": 50.0, "hard": 80.0}

# metric band half-width: "desirable difficulty" = |b - true_theta| < BAND (logits)
BAND = 1.0

# equal study budget per arm
N_ITEMS = 60
N_SEEDS = 200


def difficulty_to_logit(difficulty: float) -> float:
    """adaptive.rs::difficulty_to_logit -- b = (d/100 - 0.5) * SCALE."""
    return (difficulty / 100.0 - 0.5) * SCALE


def sigmoid(x: float) -> float:
    """adaptive.rs::sigmoid."""
    # guard against overflow in exp for extreme logits
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def estimate_theta(obs):
    """adaptive.rs::estimate_ability (theta component).

    Maximise the Rasch 1PL log-likelihood over answered observations
    (b, passed, total) with Newton's method, starting from theta = 0, clamping
    each step to [-THETA_BOUND, THETA_BOUND]; theta = 0 with no observations.
    """
    if not obs:
        return 0.0
    theta = 0.0
    for _ in range(NEWTON_ITERS):
        grad = 0.0
        hess = 0.0
        for (b, passed, total) in obs:
            p = sigmoid(theta - b)
            grad += passed - total * p
            hess -= total * p * (1.0 - p)
        if abs(hess) < 1e-6:      # flat surface (saturated) -> stop, don't divide
            break
        step = grad / hess
        theta = min(max(theta - step, -THETA_BOUND), THETA_BOUND)
        if abs(step) < 1e-5:
            break
    return min(max(theta, -THETA_BOUND), THETA_BOUND)


# ---------------------------------------------------------------------------
# Deck difficulty distribution -- from content/items.json + ai_difficulty.json,
# read exactly the way adaptive.rs::note_difficulty does (prefer AI-rated, else
# coarse difficulty::easy|medium|hard -> 20/50/80).
# ---------------------------------------------------------------------------

CONTENT = Path(__file__).resolve().parent.parent  # .../content


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


def load_deck():
    """Return list of item dicts: {id, topic, b (logit), difficulty (0-100)}.

    Difficulty precedence mirrors adaptive.rs::note_difficulty:
    aidiff (tag or, in our data, the AI rating in ai_difficulty.json) then coarse.
    Falls back to a reasonable spread only if the data files are missing.
    """
    items_path = CONTENT / "items.json"
    aidiff_path = CONTENT / "ai_difficulty.json"
    if not items_path.exists():
        # reasonable-spread fallback: 60 items, difficulty ~ N(50, 20) clamped 0-100
        rng = random.Random(0)
        out = []
        for i in range(120):
            d = min(max(rng.gauss(50, 20), 0), 100)
            out.append({"id": f"SYN-{i}", "topic": "Synthetic", "difficulty": d,
                        "b": difficulty_to_logit(d)})
        return out, "synthetic-fallback (N(50,20) clamped)"

    items = json.loads(items_path.read_text())["items"]
    aidiff = {}
    if aidiff_path.exists():
        aidiff = json.loads(aidiff_path.read_text())

    out = []
    for it in items:
        tags = it.get("tags", [])
        d = _aidiff_from_tags(tags)
        if d is None:
            j = aidiff.get(it["id"])
            if j and "ai_difficulty" in j:
                d = float(j["ai_difficulty"])
        if d is None:
            d = _coarse_from_tags(tags)
        if d is None:
            continue  # adaptive.rs: no difficulty tag -> absent from fit map
        out.append({"id": it["id"], "topic": it.get("topic", "?"),
                    "difficulty": d, "b": difficulty_to_logit(d)})
    n_ai = sum(1 for it in items if it["id"] in aidiff)
    src = (f"content/items.json ({len(out)} items with difficulty; "
           f"{n_ai} AI-rated via ai_difficulty.json, rest coarse easy/med/hard=20/50/80)")
    return out, src


# ---------------------------------------------------------------------------
# Points-at-stake weight (OFF arm) -- proxy for
# scheduler::topic_mastery::points_at_stake_weights = topic_weight x weakness.
# topic_weight comes from items.json topic_weights (default 1.0); weakness is the
# student's per-topic miss rate. sort_review orders highest weight first. This is
# a FIXED, non-adaptive order (recomputed from history but not from an ability
# estimate steering item difficulty).
# ---------------------------------------------------------------------------

def load_topic_weights():
    items_path = CONTENT / "items.json"
    if not items_path.exists():
        return {}
    tw = json.loads(items_path.read_text()).get("topic_weights", {})
    return {k: float(v) for k, v in tw.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# One simulated study session under a given policy.
# ---------------------------------------------------------------------------

def run_session(deck, topic_weights, true_theta, policy, seed, n_items):
    """Simulate n_items attempts under `policy`. Returns (in_band_fraction,
    abs_theta_error_after_N)."""
    rng = random.Random(seed)
    pool = list(deck)

    obs = []           # (b, passed, total) answered observations -> theta_hat
    per_item = {}      # id -> [passed, total] to aggregate repeats like the app
    topic_stats = {}   # topic -> [misses, seen] for the points-at-stake weakness

    in_band = 0

    # PLAIN uses a fixed shuffled DB order; OFF/ADAPTIVE re-rank each step.
    if policy == "PLAIN":
        plain_order = pool[:]
        rng.shuffle(plain_order)

    for step in range(n_items):
        theta_hat = estimate_theta(obs)

        if policy == "ADAPTIVE":
            # adaptive.rs: pick item whose b is nearest CURRENT theta_hat.
            item = min(pool, key=lambda it: (abs(it["b"] - theta_hat),
                                             _tie(rng, it)))
        elif policy == "OFF":
            # sort_review: highest points-at-stake weight first = weakest topic.
            # weight = topic_weight x weakness(topic); weakness = miss rate so far
            # (unseen topics get a neutral 0.5 so they aren't starved at start).
            def weight(it):
                tw = topic_weights.get(it["topic"], 1.0)
                misses, seen = topic_stats.get(it["topic"], (0, 0))
                weakness = (misses / seen) if seen else 0.5
                return tw * weakness
            item = max(pool, key=lambda it: (weight(it), _tie(rng, it)))
        elif policy == "PLAIN":
            item = plain_order[step % len(plain_order)]
        else:
            raise ValueError(policy)

        # (a) desirable-difficulty band, judged against the TRUE ability.
        if abs(item["b"] - true_theta) < BAND:
            in_band += 1

        # answer: correct with P = sigmoid(true_theta - b)
        correct = rng.random() < sigmoid(true_theta - item["b"])

        # record observation (aggregate repeats per item, like the app's revlog)
        pt = per_item.setdefault(item["id"], [0, 0])
        pt[1] += 1
        if correct:
            pt[0] += 1
        ts = topic_stats.setdefault(item["topic"], (0, 0))
        topic_stats[item["topic"]] = (ts[0] + (0 if correct else 1), ts[1] + 1)

        # rebuild obs from aggregated per-item passed/total (b keyed by item)
        b_of = {it["id"]: it["b"] for it in deck}
        obs = [(b_of[i], p, t) for i, (p, t) in per_item.items()]

    theta_final = estimate_theta(obs)
    return in_band / n_items, abs(theta_final - true_theta)


def _tie(rng, it):
    # deterministic-but-shuffled tie-breaker so equal keys don't bias by deck order
    return rng.random()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    deck, deck_src = load_deck()
    topic_weights = load_topic_weights()

    diffs = [it["difficulty"] for it in deck]
    logits = [it["b"] for it in deck]

    print("=" * 74)
    print("ADAPTIVE STUDY -- ABLATION SIMULATION (Rasch / 1PL)")
    print("=" * 74)
    print("This is a SIMULATION, not a human study: a proxy for a true 3-build,")
    print("equal-study-time human A/B/C test that was not feasible solo.")
    print()
    print(f"Deck source : {deck_src}")
    print(f"Difficulty  : N={len(diffs)}  0-100 scale  mean={statistics.mean(diffs):.1f} "
          f"sd={statistics.pstdev(diffs):.1f}  range=[{min(diffs):.0f},{max(diffs):.0f}]")
    print(f"            : logit b  mean={statistics.mean(logits):+.2f} "
          f"range=[{min(logits):+.2f},{max(logits):+.2f}]  (b=(d/100-0.5)*{SCALE})")
    print(f"Budget/arm  : N={N_ITEMS} item-attempts, EQUAL across arms")
    print(f"Seeds       : {N_SEEDS} (learners' true theta drawn once per seed, "
          f"SHARED across all 3 arms)")
    print(f"Band metric : desirable difficulty = |b - true_theta| < {BAND} logits")
    print("=" * 74)

    arms = ["ADAPTIVE", "OFF", "PLAIN"]
    band = {a: [] for a in arms}
    err = {a: [] for a in arms}

    theta_rng = random.Random(12345)
    for s in range(N_SEEDS):
        # one learner per seed; true ability spread across the ability axis so
        # results aren't specific to one learner. Same learner for all 3 arms.
        true_theta = theta_rng.uniform(-2.0, 2.0)
        for a in arms:
            # per-(seed,arm) answer stream seed derived from s so arms see the
            # same learner but independent coin flips are reproducible.
            b, e = run_session(deck, topic_weights, true_theta, a,
                               seed=1000 + s, n_items=N_ITEMS)
            band[a].append(b)
            err[a].append(e)

    print()
    print(f"RESULTS  (mean +/- sd over {N_SEEDS} seeds, N={N_ITEMS} attempts/arm)")
    print("-" * 74)
    print(f"{'Arm':<10} {'% items in desirable band':<30} {'|theta_hat - true_theta| @ N':<28}")
    print(f"{'':<10} {'(higher = better)':<30} {'(lower = better)':<28}")
    print("-" * 74)
    for a in arms:
        bmean = statistics.mean(band[a]) * 100
        bsd = statistics.pstdev(band[a]) * 100
        emean = statistics.mean(err[a])
        esd = statistics.pstdev(err[a])
        print(f"{a:<10} {bmean:6.1f}% +/- {bsd:4.1f}%{'':<13} "
              f"{emean:6.3f} +/- {esd:5.3f}")
    print("-" * 74)

    # relative summary
    ba, bo, bp = (statistics.mean(band[a]) * 100 for a in arms)
    ea, eo, ep = (statistics.mean(err[a]) for a in arms)
    print()
    print("Summary:")
    print(f"  Band  : ADAPTIVE {ba:.1f}%  vs OFF {bo:.1f}%  vs PLAIN {bp:.1f}%  "
          f"(adaptive keeps {ba - bp:+.1f} pts more items well-targeted vs plain)")
    print(f"  Error : ADAPTIVE {ea:.3f}  vs OFF {eo:.3f}  vs PLAIN {ep:.3f}  "
          f"(adaptive theta error is {(1 - ea / ep) * 100:.0f}% lower than plain)")
    print()
    print("Interpretation: with study budget held equal, ADAPTIVE should show the")
    print("highest in-band fraction and the lowest ability-estimation error.")
    print("These are the ACTUAL numbers from this run; re-running is deterministic")
    print("(fixed seeds).")

    # emit machine-readable line for the docs to cite
    result = {
        "n_items": N_ITEMS, "n_seeds": N_SEEDS, "band": BAND, "scale": SCALE,
        "deck_n": len(diffs), "deck_mean_diff": round(statistics.mean(diffs), 2),
        "arms": {a: {"band_pct_mean": round(statistics.mean(band[a]) * 100, 2),
                     "band_pct_sd": round(statistics.pstdev(band[a]) * 100, 2),
                     "theta_err_mean": round(statistics.mean(err[a]), 4),
                     "theta_err_sd": round(statistics.pstdev(err[a]), 4)}
                 for a in arms},
    }
    print()
    print("JSON:" + json.dumps(result))


if __name__ == "__main__":
    main()
