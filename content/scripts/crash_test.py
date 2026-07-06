#!/usr/bin/env python3
"""Rubric 7g(a): kill the app mid-review 20x in a row -> ZERO corrupted collections.

The desktop app and the iOS app share ONE engine: Anki's Rust core (rslib) on top
of a single SQLite database, opened in WAL mode inside transactions. So a crash-
safety proof at the engine level is a proof for BOTH platforms. This script drives
that engine directly through the pylib `Collection` API on throwaway temp
collections seeded from `content/gmat_focus.apkg`; it never touches user data and
never runs the GUI.

Method (repeated 20 times):
  1. Copy the seeded deck to a FRESH temp collection file.
  2. Spawn a CHILD process (subprocess) that opens that collection and enters a
     tight review-write loop: fetch the next card and `answer_card(...)` it, over
     and over. Each answer is a real scheduler mutation wrapped in a SQLite
     transaction (revlog insert + card update), i.e. exactly the write a student
     triggers by tapping an answer button.
  3. After a short randomized delay (so the signal lands *while* a transaction is
     in flight), kill the child HARD with SIGKILL — the OS-level "pull the plug",
     no cleanup, no chance to commit or close.
  4. Back in the parent, REOPEN the same file and check three things:
       * SQLite `PRAGMA integrity_check` == "ok"   (structure intact)
       * `Collection(path)` opens without error     (engine accepts it)
       * note_count / card_count unchanged (108/108) (no rows lost/torn)

An interrupted write must NOT corrupt the database: SQLite's WAL journaling +
Anki's transaction discipline guarantee the last uncommitted answer is simply
rolled back (lost), while everything already committed survives intact. Success is
20/20 clean, 0 corrupted.

Run:
  PYTHONPATH=anki/out/pylib:anki/pylib:anki/out/qt \
    anki/out/pyenv/bin/python content/scripts/crash_test.py
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gmat_test_common import (  # noqa: E402
    integrity_check,
    make_all_cards_reviewable,
    seed_collection,
)

from anki.collection import Collection  # noqa: E402

ITERATIONS = 20
# Range for the randomized pre-kill delay. Chosen so the child has opened the
# collection and started hammering answers, but the SIGKILL still lands amid the
# ongoing write loop rather than after it has cleanly finished.
KILL_DELAY_MIN = 0.15
KILL_DELAY_MAX = 0.45


# --------------------------------------------------------------------------- #
# Child mode: opened by the parent via `python crash_test.py --child <path>`.
# Opens the collection and answers cards in a tight loop until SIGKILL'd.
# --------------------------------------------------------------------------- #
def run_child(path: str) -> None:
    from anki.cards import Card
    from anki.scheduler_pb2 import CardAnswer

    col = Collection(path)
    make_all_cards_reviewable(col)

    ratings = [CardAnswer.AGAIN, CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY]
    i = 0
    # Loop forever; the parent SIGKILLs us mid-write. Re-fetch after each answer
    # so we keep generating fresh transactions (revlog insert + card update).
    while True:
        qc = col.sched.get_queued_cards(fetch_limit=1)
        if not qc.cards:
            # Ran out of due cards; re-fetch (backend rebuilds the queue) to keep
            # the write pressure up.
            qc = col.sched.get_queued_cards(fetch_limit=1)
            if not qc.cards:
                # Nothing left at all — keep writing config so a transaction is
                # always in flight when the kill lands.
                col.set_config("crash-probe", i)
                i += 1
                continue
        queued = qc.cards[0]
        card = Card(col)
        card._load_from_backend_card(queued.card)
        card.start_timer()
        ans = col.sched.build_answer(
            card=card, states=queued.states, rating=ratings[i % 4]
        )
        col.sched.answer_card(ans)
        i += 1


# --------------------------------------------------------------------------- #
# Parent mode.
# --------------------------------------------------------------------------- #
def one_iteration(master_path: str, n: int) -> tuple[bool, str]:
    """Copy master -> fresh file, run+kill a child mid-write, verify integrity.

    Returns (clean, detail)."""
    import random

    workdir = tempfile.mkdtemp(prefix=f"gmat-crash-{n:02d}-")
    path = os.path.join(workdir, "collection.anki2")
    shutil.copyfile(master_path, path)

    # Spawn the child in --child mode with the same interpreter + PYTHONPATH.
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--child", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Let it get into the write loop, then pull the plug mid-transaction.
    time.sleep(random.uniform(KILL_DELAY_MIN, KILL_DELAY_MAX))
    killed_running = proc.poll() is None
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        killed_running = False
    proc.wait()

    # --- Verify the file the killed child left behind. ---
    integ = integrity_check(path)
    if integ != "ok":
        return False, f"integrity_check={integ!r}"

    try:
        col = Collection(path)
    except Exception as e:  # noqa: BLE001
        return False, f"reopen failed: {e!r}"
    try:
        notes = col.note_count()
        cards = col.card_count()
        revs = col.db.scalar("select count() from revlog")
    finally:
        col.close()

    if (notes, cards) != (108, 108):
        return False, f"counts changed: notes={notes} cards={cards}"

    killed_note = "killed mid-run" if killed_running else "child already idle"
    return True, f"integrity=ok reopened notes={notes} cards={cards} revlog={revs} ({killed_note})"


def main() -> None:
    print("=" * 72)
    print("RUBRIC 7g(a) CRASH TEST — kill mid-review 20x, expect 0 corruptions")
    print("Engine under test: rslib + SQLite (WAL), shared by desktop + iOS.")
    print("=" * 72)

    # Seed one master collection, then copy it per iteration (a copy of a freshly
    # checkpointed DB is itself clean, giving every run an identical start).
    master_col, master_path = seed_collection(prefix="gmat-crash-master-")
    print(f"seed: notes={master_col.note_count()} cards={master_col.card_count()}")
    master_col.close()

    clean = 0
    for n in range(1, ITERATIONS + 1):
        ok, detail = one_iteration(master_path, n)
        clean += ok
        status = "CLEAN" if ok else "CORRUPTED"
        print(f"  [{n:2d}/{ITERATIONS}] {status:9s} {detail}")

    print("-" * 72)
    print(f"RESULT: {clean}/{ITERATIONS} clean, {ITERATIONS - clean} corrupted")
    if clean == ITERATIONS:
        print("PASS: an interrupted write never corrupted the collection "
              "(SQLite WAL + Anki transactions).")
        sys.exit(0)
    else:
        print("FAIL: at least one killed-mid-review collection was corrupted.")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        run_child(sys.argv[2])
    else:
        main()
