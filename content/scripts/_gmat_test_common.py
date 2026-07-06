#!/usr/bin/env python3
"""Shared helpers for the rubric-7g crash + offline engine tests.

Both tests run against the SHARED engine (rslib + SQLite) via the pylib
`Collection` API, on throwaway temp collections seeded from
`content/gmat_focus.apkg`. Because desktop (aqt) and phone (iOS bridge) both
drive the *same* rslib engine and the same SQLite file format, exercising the
engine here covers both platforms.

Nothing in here touches user data: every collection is created under a fresh
`tempfile.mkdtemp()` directory.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from anki.collection import Collection
import anki.import_export_pb2 as ie

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APKG = os.path.join(REPO, "content", "gmat_focus.apkg")


def seed_collection(prefix: str = "gmat-7g-") -> tuple[Collection, str]:
    """Create a fresh temp collection seeded from the shipped GMAT deck.

    Returns (open Collection, path-to-.anki2). Caller owns closing it.
    """
    if not os.path.exists(APKG):
        raise SystemExit(f"missing seed deck: {APKG}")
    d = tempfile.mkdtemp(prefix=prefix)
    path = os.path.join(d, "collection.anki2")
    col = Collection(path)
    opts = ie.ImportAnkiPackageOptions(with_scheduling=True, with_deck_configs=True)
    col._backend.import_anki_package(package_path=APKG, options=opts)
    return col, path


def make_all_cards_reviewable(col: Collection) -> int:
    """Point the reviewer at the parent GMAT deck and lift the daily limits so
    every seeded card is actually queued. Returns the id of the deck now
    selected. This is a config change only (no card mutation)."""
    # The deck holding the most cards, and its top-level parent, so the queue
    # spans every subdeck.
    rows = col.db.all("select did, count() from cards group by did")
    main_did = max(rows, key=lambda r: r[1])[0]
    name = col.decks.name(main_did)
    top = name.split("::", 1)[0]
    parent_did = col.decks.id(top)

    # Bump per-day limits on every deck's config so nothing is throttled.
    seen = set()
    for did, _ in rows + [(parent_did, 0)]:
        conf = col.decks.config_dict_for_deck_id(did)
        if conf["id"] in seen:
            continue
        seen.add(conf["id"])
        conf["new"]["perDay"] = 99999
        conf["rev"]["perDay"] = 99999
        col.decks.save(conf)

    col.decks.set_current(parent_did)
    return parent_did


def register_unicase(con: sqlite3.Connection) -> None:
    """Anki registers a custom `unicase` collation on its SQLite connection
    (rslib/src/storage/sqlite.rs). A raw sqlite3 connection doesn't have it, so
    `PRAGMA integrity_check` — which walks indexes that COLLATE unicase — errors
    with 'no such collation sequence'. Register an ASCII case-insensitive
    collation (matches rslib's UniCase for ASCII tag/deck names) so the check
    can run."""

    def cmp(a: str, b: str) -> int:
        la, lb = a.lower(), b.lower()
        return (la > lb) - (la < lb)

    con.create_collation("unicase", cmp)


def integrity_check(path: str) -> str:
    """Run SQLite `PRAGMA integrity_check` on the collection file with the
    unicase collation registered. Returns the pragma's single-row result
    ('ok' when the database is structurally sound)."""
    con = sqlite3.connect(path)
    try:
        register_unicase(con)
        return con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
