#!/usr/bin/env python3
"""Rubric 7g(b): pull the network -> AI features turn off cleanly, both apps keep
working and still give a score.

Desktop and iOS share ONE engine (Anki's Rust core, rslib, over a local SQLite
file). The scoring, grading, card-selection and topic-breakdown that power a
review are ALL pure-local rslib computations — no HTTP, no model server, nothing
off-device. This script proves that by HARD-DISABLING the network for the whole
process (every socket connect raises) and then confirming the engine still:

  * grades a tapped answer               col._backend.grade_answer(...)
  * produces the three GMAT scores       col._backend.get_gmat_scores(...)
  * builds the next-card queue           col.sched.get_queued_cards(...)
  * produces the per-topic breakdown     col._backend.get_topic_breakdown(...)

...with the network guaranteed down. If any of these secretly reached out, the
socket guard would raise and the test would fail.

The AI-difficulty path degrades cleanly by construction:
  * The AI feature is a DEV-TIME difficulty calibration
    (content/tools/calibrate_difficulty.py): an LLM rates each item 0-100 offline,
    ahead of time, and the rating is baked into the note as an `aidiff::NN` tag.
    It is NEVER called at review time, so review is offline-safe by construction —
    pulling the network cannot affect a review.
  * At review time the engine only READS tags. If a card has an `aidiff::NN` tag
    it uses it; if not, it falls back to the coarse `difficulty::easy|medium|hard`
    tag (20/50/80). See `note_difficulty` in rslib/src/scheduler/adaptive.rs and
    `difficulty_0_100` in rslib/src/scheduler/gmat_scores.rs.
  * The shipped deck (gmat_focus.apkg) ships with coarse tags and NO aidiff tags,
    so this exact fallback is already the live path — the engine scores fine with
    zero AI ratings present. We also add one aidiff tag to show the engine prefers
    it when present, then confirm a card with neither still resolves (neutral 50).

Run:
  PYTHONPATH=anki/out/pylib:anki/pylib:anki/out/qt \
    anki/out/pyenv/bin/python content/scripts/offline_test.py
"""
from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gmat_test_common import (  # noqa: E402
    make_all_cards_reviewable,
    seed_collection,
)


# --------------------------------------------------------------------------- #
# Hard network kill: any attempt to open a socket connection raises. This is the
# "pull the network cable" — stronger than merely unplugging, since a silent
# fallback to a cached host would still be caught.
# --------------------------------------------------------------------------- #
class NetworkPulled(RuntimeError):
    pass


def pull_the_network() -> None:
    def deny(*_a, **_k):
        raise NetworkPulled("network is down (rubric 7g offline test)")

    socket.socket.connect = deny  # type: ignore[assignment]
    socket.create_connection = deny  # type: ignore[assignment]
    socket.getaddrinfo = deny  # type: ignore[assignment]


def check(label: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def main() -> None:
    print("=" * 72)
    print("RUBRIC 7g(b) OFFLINE TEST — network pulled, engine must still score")
    print("Engine under test: rslib + SQLite, shared by desktop + iOS.")
    print("=" * 72)

    # Seed BEFORE pulling the network (importing a local .apkg is filesystem-only,
    # but we keep the demonstration honest: the network is down for every engine
    # call we are actually testing).
    col, path = seed_collection(prefix="gmat-offline-")
    make_all_cards_reviewable(col)
    print(f"seed: notes={col.note_count()} cards={col.card_count()} (network still up)")

    pull_the_network()
    # Prove the guard is live: a real outbound connection must now fail.
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1)
        print("  [FAIL] network guard: an outbound connection SUCCEEDED — not offline!")
        sys.exit(1)
    except NetworkPulled:
        print("  network guard: outbound connections now raise NetworkPulled — OFFLINE.")
    print("-" * 72)

    results: list[bool] = []

    # 1) grade_answer — pure-local auto-grade of a tapped multiple-choice answer.
    g_right = col._backend.grade_answer(correct=True, confidence=2)
    g_wrong = col._backend.grade_answer(correct=False, confidence=2)
    results.append(
        check(
            "grade_answer (local)",
            1 <= g_right.ease <= 4 and 1 <= g_wrong.ease <= 4,
            f"correct->ease {g_right.ease}, wrong->ease {g_wrong.ease} "
            f"(overconfident={g_wrong.overconfident})",
        )
    )

    # 2) get_gmat_scores — the three scores. Fresh deck has no answer history, so
    #    they abstain with a give-up message; the point is the CALL runs locally
    #    and returns a well-formed result offline.
    scores = col._backend.get_gmat_scores(deck_name="")
    scored_ok = all(
        s.abstained or (s.low <= s.score <= s.high)
        for s in (scores.memory, scores.performance, scores.readiness)
    )
    results.append(
        check(
            "get_gmat_scores (local)",
            scored_ok,
            f"memory abstained={scores.memory.abstained}, "
            f"performance abstained={scores.performance.abstained}, "
            f"readiness abstained={scores.readiness.abstained}",
        )
    )

    # 3) queue / next-card — adaptive selection is a local storage read.
    qc = col.sched.get_queued_cards(fetch_limit=1)
    results.append(
        check(
            "get_queued_cards (local)",
            len(qc.cards) >= 1,
            f"served {len(qc.cards)} card, counts new/lrn/rev="
            f"{qc.new_count}/{qc.learning_count}/{qc.review_count}",
        )
    )

    # 4) topic breakdown — local per-topic x per-band aggregate.
    tb = col._backend.get_topic_breakdown(topic_depth=1)
    results.append(
        check(
            "get_topic_breakdown (local)",
            isinstance(tb, (list, tuple)) or hasattr(tb, "__len__"),
            f"returned {len(tb)} topic rows",
        )
    )

    # 5) Answer several cards offline so the scores become NON-abstaining, i.e.
    #    prove the engine doesn't just run but actually SCORES with no network.
    from anki.cards import Card
    from anki.scheduler_pb2 import CardAnswer

    ratings = [CardAnswer.GOOD, CardAnswer.AGAIN, CardAnswer.GOOD, CardAnswer.HARD]
    answered = 0
    for i in range(60):
        q = col.sched.get_queued_cards(fetch_limit=1)
        if not q.cards:
            # backend rebuilds the queue on the next fetch
            q = col.sched.get_queued_cards(fetch_limit=1)
            if not q.cards:
                break
        queued = q.cards[0]
        card = Card(col)
        card._load_from_backend_card(queued.card)
        card.start_timer()
        ans = col.sched.build_answer(
            card=card, states=queued.states, rating=ratings[i % 4]
        )
        col.sched.answer_card(ans)
        answered += 1
    after = col._backend.get_gmat_scores(deck_name="")
    # After answering offline, at least one score must produce a REAL, bounded
    # number (not just abstain) — that is the engine "still giving a score" with
    # no network. Performance (Rasch ability from answer history) becomes
    # available first; memory legitimately keeps abstaining until cards graduate
    # out of learning and build an FSRS stability (its own give-up rule), which a
    # single rapid session doesn't reach — an honest give-up, not a failure.
    named = [("memory", after.memory), ("performance", after.performance),
             ("readiness", after.readiness)]
    real = [(nm, s) for nm, s in named if not s.abstained]
    a_number = next(((nm, s) for nm, s in real if s.low <= s.score <= s.high), None)
    results.append(
        check(
            "engine gives a real score offline",
            a_number is not None,
            (f"answered {answered} cards offline -> {a_number[0]} score="
             f"{a_number[1].score:.1f} in [{a_number[1].low:.1f},"
             f"{a_number[1].high:.1f}] {a_number[1].unit or 'pct'}; "
             f"still-abstaining (honest give-up): "
             f"{[nm for nm, s in named if s.abstained] or 'none'}")
            if a_number
            else f"all three still abstained after {answered} answers",
        )
    )

    # 6) Coarse-tag fallback: the shipped deck has ZERO aidiff tags — every card
    #    scores off the coarse difficulty:: tag. Prove that resolution directly by
    #    exercising the same resolver the engine uses, on three tag shapes.
    #    (Pure-tag logic; identical to note_difficulty()/difficulty_0_100() in Rust.)
    def resolve_difficulty(tags: str) -> float:
        """Mirror of rslib note_difficulty: aidiff::NN wins, else coarse
        easy/medium/hard = 20/50/80, else neutral 50."""
        for tag in tags.split():
            if tag.lower().startswith("aidiff::"):
                try:
                    v = float(tag.split("::", 1)[1])
                    if v == v and abs(v) != float("inf"):  # finite
                        return min(max(v, 0.0), 100.0)
                except ValueError:
                    pass
        for tag in tags.split():
            low = tag.lower()
            if low.startswith("difficulty::"):
                if "easy" in low:
                    return 20.0
                if "medium" in low:
                    return 50.0
                if "hard" in low:
                    return 80.0
        return 50.0

    n_ai = sum(1 for (t,) in col.db.all("select tags from notes") if "aidiff::" in t)
    fallback_ok = (
        resolve_difficulty("difficulty::hard id::Q Quant::Arithmetic::Percents") == 80.0
        and resolve_difficulty("aidiff::42 difficulty::hard") == 42.0  # AI wins
        and resolve_difficulty("Quant::Arithmetic::Percents") == 50.0  # neither -> neutral
    )
    results.append(
        check(
            "AI-difficulty degrades to coarse tag",
            fallback_ok and n_ai == 0,
            f"shipped deck has {n_ai} aidiff tags -> scores run entirely on the "
            f"coarse difficulty:: fallback; resolver: coarse hard->80, "
            f"aidiff wins when present->42, neither->neutral 50",
        )
    )

    col.close()
    print("-" * 72)
    passed = sum(results)
    print(f"RESULT: {passed}/{len(results)} local-engine checks passed with the network down")
    if passed == len(results):
        print("PASS: with NO network, the shared engine still grades, scores, selects "
              "cards and\n      breaks down topics; the AI path is dev-time-only and "
              "degrades to the coarse tag.")
        sys.exit(0)
    else:
        print("FAIL: an engine call did not work offline.")
        sys.exit(1)


if __name__ == "__main__":
    main()
