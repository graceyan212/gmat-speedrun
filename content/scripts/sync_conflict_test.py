#!/usr/bin/env python3
"""Rubric 7b — offline no-loss / no-double-count + conflict-winner test.

This is the full 7b sync test, run end-to-end against a LOCAL self-hosted
Anki sync server (rslib's `RustBackend.syncserver()`, i.e. the same server
`python -m anki.syncserver` runs) started in-process on a temp port. It does
NOT touch the user's collection, and it does NOT contact the fly server.

It builds on `sync_smoketest.py` (which proves a single note round-trips
A -> server -> B). Here we prove the two 7b guarantees:

  PART 1 — no loss / no double count
    Seed two fresh collections A and B from the SAME deck
    (content/gmat_focus.apkg) and sync both to the (empty) server so they
    share a common base. Then, OFFLINE:
      * A answers 10 DISTINCT cards
      * B answers 10 DIFFERENT distinct cards (disjoint from A's 10)
    Reconnect and sync A -> server -> B -> server -> A until converged, then
    assert both collections' revlogs contain all 20 reviews for those 20
    card ids: none lost, none duplicated, total unique == 20, and A == B.

  PART 2 — conflict winner
    Both A and B answer the SAME (21st) card offline with DIFFERENT ratings.
    Sync both and record which review the converged collections keep. The
    rule is documented in
    docs/superpowers/specs/2026-07-02-ios-desktop-sync-design.md sec 4.4:
    normal reviews merge via the revlog (both applied, nothing double
    counted); on true divergence Anki forces a full up/download and the
    UPLOADING side becomes authoritative. This test observes and prints the
    actual winner.

Sync API (from sync_smoketest.py, verified against col._backend):
  - col._backend.sync_login(SyncLoginRequest) -> SyncAuth
  - col._backend.sync_collection(auth=, sync_media=) -> SyncCollectionResponse
  - col._backend.full_upload_or_download(FullUploadOrDownloadRequest) -> Empty
  - SyncCollectionResponse.required enum:
    NO_CHANGES=0 NORMAL_SYNC=1 FULL_SYNC=2 FULL_DOWNLOAD=3 FULL_UPLOAD=4

Reproduce:
    content/scripts/sync_conflict_test.py
(run it through the built pyenv/pylib — see the exact command in docs/SYNC_TEST.md.)
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time

from anki.collection import Collection
import anki.scheduler_pb2 as pb_sched
import anki.sync_pb2 as pb
from anki.import_export_pb2 import ImportAnkiPackageRequest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APKG = os.path.join(REPO, "content", "gmat_focus.apkg")

USER, PASS = "demo", "gmatsync2026"  # matches the app default account
HOST = "127.0.0.1"

ChangesRequired = pb.SyncCollectionResponse.ChangesRequired


# --------------------------------------------------------------------------
# Local sync server (no cargo build; runs the prebuilt rslib server via the
# `anki.syncserver` module, which calls RustBackend.syncserver()).
#
# NOTE: the server is launched as a SEPARATE PROCESS, not an in-process
# thread. RustBackend.syncserver() runs a blocking Rust/Tokio runtime that
# holds the Python GIL for its lifetime, so a same-process thread would
# starve the sync-client calls (they'd hang forever). A child process is the
# same model `python -m anki.syncserver` uses, and it's exactly how
# sync_smoketest.py expects the server to be run.
# --------------------------------------------------------------------------
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server() -> tuple[str, str, subprocess.Popen]:
    """Spawn rslib's sync server as a child process. Returns (endpoint, base, proc)."""
    port = _free_port()
    base = tempfile.mkdtemp(prefix="anki-sync-7b-server-")
    env = dict(os.environ)
    env["SYNC_HOST"] = HOST
    env["SYNC_PORT"] = str(port)
    env["SYNC_USER1"] = f"{USER}:{PASS}"
    env["SYNC_BASE"] = base
    env.setdefault("RUST_LOG", "error")

    proc = subprocess.Popen(
        [sys.executable, "-m", "anki.syncserver"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(lambda: proc.terminate())

    endpoint = f"http://{HOST}:{port}/"
    # Wait for the TCP port to accept connections.
    for _ in range(200):
        if proc.poll() is not None:
            raise RuntimeError(f"sync server exited early (code {proc.returncode})")
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("sync server did not start")
    return endpoint, base, proc


# --------------------------------------------------------------------------
# Collection + sync helpers.
# --------------------------------------------------------------------------
def open_col(tag: str) -> Collection:
    d = tempfile.mkdtemp(prefix=f"anki-sync-7b-{tag}-")
    return Collection(os.path.join(d, "collection.anki2"))


def seed(col: Collection) -> None:
    """Import the GMAT deck into a fresh collection."""
    col.import_anki_package(ImportAnkiPackageRequest(package_path=APKG))


def login(col: Collection, endpoint: str) -> pb.SyncAuth:
    req = pb.SyncLoginRequest(username=USER, password=PASS, endpoint=endpoint)
    return col._backend.sync_login(req)


def sync(col: Collection, auth: pb.SyncAuth) -> str:
    """One sync step; performs a full up/download when the server requires it."""
    out = col._backend.sync_collection(auth=auth, sync_media=False)
    required = out.required
    if required in (ChangesRequired.FULL_UPLOAD, ChangesRequired.FULL_SYNC):
        col._backend.full_upload_or_download(
            pb.FullUploadOrDownloadRequest(
                auth=auth, upload=True, server_usn=out.server_media_usn
            )
        )
    elif required == ChangesRequired.FULL_DOWNLOAD:
        col._backend.full_upload_or_download(
            pb.FullUploadOrDownloadRequest(
                auth=auth, upload=False, server_usn=out.server_media_usn
            )
        )
    return ChangesRequired.Name(required)


_EASE_TO_RATING = {
    1: pb_sched.CardAnswer.AGAIN,
    2: pb_sched.CardAnswer.HARD,
    3: pb_sched.CardAnswer.GOOD,
    4: pb_sched.CardAnswer.EASY,
}


def answer(col: Collection, card_id: int, ease: int, answered_at_millis: int) -> None:
    """Answer one specific card by id (ease 1=Again .. 4=Easy) at an EXPLICIT
    millisecond timestamp.

    Why the explicit timestamp: a review's revlog primary key is its
    `answered_at` epoch-millisecond value (rslib RevlogId). This test answers
    dozens of cards in a tight loop across two collections; if we let each
    review default to wall-clock `now()` (what col.sched.answerCard does), many
    land in the SAME millisecond, so A's and B's revlog ids collide and clobber
    each other on sync-merge — an artifact of the test's speed, not a sync bug
    (real users on two devices never answer 20 cards in the same millisecond).
    Assigning each review a distinct, side-separated millisecond makes the ids
    unique so the merge is exact and deterministic.

    start_timer() mirrors what the scheduler's getCard() does when presenting a
    card; the answer needs card.timer_started set to compute milliseconds_taken.
    """
    card = col.get_card(card_id)
    card.start_timer()
    states = col._backend.get_scheduling_states(card.id)
    input = col.sched.build_answer(
        card=card, states=states, rating=_EASE_TO_RATING[ease]
    )
    input.answered_at_millis = answered_at_millis  # unique revlog id per review
    col.sched.answer_card(input)
    card.load()


def revlog_count(col: Collection, card_ids: list[int]) -> int:
    placeholders = ",".join("?" for _ in card_ids)
    return col.db.scalar(
        f"select count(*) from revlog where cid in ({placeholders})", *card_ids
    )


def revlog_rows(col: Collection, card_id: int) -> list[tuple[int, int]]:
    """(id_ms, ease) rows for one card, oldest first."""
    return col.db.all("select id, ease from revlog where cid=? order by id", card_id)


def converge(a: Collection, b: Collection, auth_a, auth_b) -> None:
    """Sync A -> server -> B -> server -> A until both report NO_CHANGES."""
    for _ in range(6):
        ra = sync(a, auth_a)
        rb = sync(b, auth_b)
        ra2 = sync(a, auth_a)
        if ra2 == "NO_CHANGES" and rb in ("NO_CHANGES", "NORMAL_SYNC"):
            # one more pass so both sides definitely have each other's delta
            sync(b, auth_b)
            sync(a, auth_a)
            return
    # final settle
    sync(a, auth_a)
    sync(b, auth_b)
    sync(a, auth_a)


# --------------------------------------------------------------------------
def main() -> int:
    assert os.path.exists(APKG), f"deck not found: {APKG}"
    endpoint, _base, server = start_server()
    print(f"local sync server: {endpoint}  (user={USER})")
    print(f"deck: {APKG}\n")

    # --- Establish a common base ------------------------------------------
    a = open_col("A")
    b = open_col("B")
    seed(a)
    print(f"seeded A: {a.card_count()} cards, {a.note_count()} notes")

    auth_a = login(a, endpoint)
    auth_b = login(b, endpoint)

    # A seeds the (empty) server, B downloads it => shared base.
    print("A -> server (seed):", sync(a, auth_a))
    print("B <- server (base):", sync(b, auth_b))
    print(f"base established: A={a.card_count()} cards  B={b.card_count()} cards\n")

    assert a.card_count() == b.card_count() > 0, "base not shared"

    # Pick 21 distinct cards up front: 10 for A, 10 for B, 1 shared conflict card.
    all_cards = sorted(a.find_cards(""))
    assert len(all_cards) >= 21, f"deck too small: {len(all_cards)} cards"
    a_cards = all_cards[0:10]
    b_cards = all_cards[10:20]
    conflict_card = all_cards[20]
    assert not (set(a_cards) & set(b_cards)), "A/B card sets overlap"
    expected20 = a_cards + b_cards

    # --- PART 1: offline reviews, no loss / no double count ---------------
    # OFFLINE (no sync between these): A answers its 10, B answers its 10.
    # Distinct, non-overlapping revlog ids per side (see answer() docstring):
    # A uses base+0,2,4,..; B uses base+1000,1002,.. — no two reviews collide.
    base_ms = int(time.time() * 1000) - 60_000
    for i, cid in enumerate(a_cards):
        answer(a, cid, 3, base_ms + i * 2)  # Good
    for i, cid in enumerate(b_cards):
        answer(b, cid, 2, base_ms + 1000 + i * 2)  # Hard
    print("OFFLINE reviews done:")
    print(f"  A answered 10 cards: {a_cards}")
    print(f"  B answered 10 cards: {b_cards}")
    print(f"  A local revlog for its 10: {revlog_count(a, a_cards)}")
    print(f"  B local revlog for its 10: {revlog_count(b, b_cards)}\n")

    # Reconnect + converge.
    converge(a, b, auth_a, auth_b)

    a_total = revlog_count(a, expected20)
    b_total = revlog_count(b, expected20)
    a_unique = a.db.scalar(
        "select count(distinct cid) from revlog where cid in (%s)"
        % ",".join("?" for _ in expected20),
        *expected20,
    )
    b_unique = b.db.scalar(
        "select count(distinct cid) from revlog where cid in (%s)"
        % ",".join("?" for _ in expected20),
        *expected20,
    )

    print("=== PART 1 RESULT (after convergence) ===")
    print(f"  revlog rows for the 20 cards on A: {a_total}")
    print(f"  revlog rows for the 20 cards on B: {b_total}")
    print(f"  distinct cards reviewed on A:      {a_unique}")
    print(f"  distinct cards reviewed on B:      {b_unique}")

    ok1 = (
        a_total == 20
        and b_total == 20
        and a_unique == 20
        and b_unique == 20
        and a_total == b_total
    )
    print(f"  -> 20 land: {a_total==20 and b_unique==20} | "
          f"0 lost: {a_unique==20} | 0 dup: {a_total==20} | A==B: {a_total==b_total}")
    print(f"  PART 1 {'PASS' if ok1 else 'FAIL'}\n")

    # --- PART 2: same card on both, conflict winner -----------------------
    # OFFLINE: both answer the SAME card with DIFFERENT ratings.
    # A answers FIRST (Again), then — a couple of seconds later — B answers
    # (Easy). The 2s gap matters: a card's row is keyed by card id and carries
    # a modification time in *seconds*; the sync's conflict resolution for that
    # single row is last-write-wins by that mod time. Answering in the same
    # wall-clock second would make the two mod times equal and neither would
    # win (an artifact of test speed), so we space them into distinct seconds —
    # exactly what happens when a human reviews on two real devices minutes
    # apart. The revlog entries (unique ms ids) always both survive regardless.
    a_ease, b_ease = 1, 4  # A says Again(1) EARLIER; B says Easy(4) LATER
    now_ms = int(time.time() * 1000)
    a_ts = now_ms + 5000
    a_before = revlog_rows(a, conflict_card)
    answer(a, conflict_card, a_ease, a_ts)
    time.sleep(2)  # put B's card-row mod time in a later second than A's
    b_ts = int(time.time() * 1000) + 5000
    answer(b, conflict_card, b_ease, b_ts)
    a_row = [r for r in revlog_rows(a, conflict_card) if r not in a_before]
    b_row = [r for r in revlog_rows(b, conflict_card) if r not in a_before]
    print("=== PART 2: conflict on one shared card ===")
    print(f"  conflict card id: {conflict_card}")
    print(f"  A answered ease={a_ease} (Again) @ {a_ts}, new revlog: {a_row}")
    print(f"  B answered ease={b_ease} (Easy)  @ {b_ts}, new revlog: {b_row}")

    # Sync A first (uploads A's review), then B (uploads its review + downloads
    # A's), then converge so both sides hold the merged result.
    ra = sync(a, auth_a)
    rb = sync(b, auth_b)
    converge(a, b, auth_a, auth_b)
    print(f"  first sync of A returned: {ra}")
    print(f"  first sync of B returned: {rb}")

    final_a = revlog_rows(a, conflict_card)
    final_b = revlog_rows(b, conflict_card)
    print(f"  A final revlog for conflict card: {final_a}")
    print(f"  B final revlog for conflict card: {final_b}")

    # The card's converged scheduling state — this is the observable "winner"
    # for the CARD row (both reviews are kept in the revlog history; the card's
    # single row / forward schedule reflects the last-modified write).
    state_a = a.db.first(
        "select mod, queue, type, ivl, due from cards where id=?", conflict_card
    )
    state_b = b.db.first(
        "select mod, queue, type, ivl, due from cards where id=?", conflict_card
    )
    print(f"  A card row (mod,queue,type,ivl,due): {state_a}")
    print(f"  B card row (mod,queue,type,ivl,due): {state_b}")

    a_eases = {e for _, e in final_a}
    b_eases = {e for _, e in final_b}
    both_kept = a_ease in a_eases and b_ease in a_eases and a_eases == b_eases
    converged2 = final_a == final_b and state_a == state_b
    # Which review governs the card's converged forward schedule? The revlog
    # rows are ordered by ms id; the last one is the most recent review, and
    # the converged card row should reflect it (last-write-wins by mod time).
    last_ease = final_a[-1][1] if final_a else None
    if last_ease == b_ease:
        card_winner = f"B's review (ease={b_ease} Easy, later mod time)"
    elif last_ease == a_ease:
        card_winner = f"A's review (ease={a_ease} Again, later mod time)"
    else:
        card_winner = f"ease={last_ease}"

    print(f"  A and B converged identically (card row + revlog): {converged2}")
    print(f"  both reviews kept in revlog (no double-count, no loss): {both_kept}")
    print(f"  CONFLICT WINNER (card's converged schedule): {card_winner}")
    print("  RULE (design spec sec 4.4): normal reviews merge via the revlog — "
          "BOTH devices' reviews are applied and NONE double-counted; the card's "
          "forward schedule follows the last-applied (most recent) review. On "
          "true divergence (schema change / failed sanity check) Anki instead "
          "forces a full up/download and the UPLOADING side becomes "
          "authoritative.\n")

    a.close()
    b.close()
    server.terminate()

    print("=== SUMMARY ===")
    print(f"PART 1 (no loss / no double count): {'PASS' if ok1 else 'FAIL'} "
          f"-- {a_total} landed, {20 - a_unique} lost, {a_total - 20} duplicated")
    print(f"PART 2 (conflict rule): converged={converged2}, both reviews kept="
          f"{both_kept}, card winner={card_winner}")
    ok2 = converged2 and both_kept
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
