#!/usr/bin/env python3
"""Force the mid-progress demo state onto an EXISTING desktop collection.

Normally the app seeds the demo state automatically on first launch — but it
holds back if the collection already has real GMAT reviews (so it never clobbers
genuine practice). Use this when that happened and the scores show "answer more
cards": it clears the GMAT deck's review history and re-seeds a clean
mid-progress state (three scores populate, coverage map fills in).

CLOSE the desktop app first (it locks the collection), then run:

    python content/tools/seed_live_collection.py
    # or point at a specific collection:
    python content/tools/seed_live_collection.py "/path/to/collection.anki2"

Re-openable and non-destructive to non-GMAT decks: it only touches cards under
the "GMAT Focus" deck tree. To undo, re-import content/gmat_focus.apkg.
"""

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ANKI = os.path.join(REPO, "anki")

# Re-exec under Anki's bundled Python (it has anki's deps, e.g. `markdown`), so
# this works no matter which `python` you invoke it with.
_PYENV = os.path.join(ANKI, "out", "pyenv", "bin", "python")
if os.path.exists(_PYENV) and os.path.realpath(sys.executable) != os.path.realpath(_PYENV):
    os.execv(_PYENV, [_PYENV, os.path.abspath(__file__), *sys.argv[1:]])

sys.path[:0] = [os.path.join(ANKI, "out", "pylib"), os.path.join(ANKI, "pylib")]

import anki.lang  # noqa: E402

anki.lang.set_lang("en")
from anki.collection import Collection  # noqa: E402
from anki.gmat_demo_seed import seed_demo_history  # noqa: E402

PARENT = "GMAT Focus"


def _find_collection():
    if len(sys.argv) > 1:
        return sys.argv[1]
    base = os.path.expanduser("~/Library/Application Support/Anki2")
    hits = glob.glob(os.path.join(base, "*", "collection.anki2"))
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"No collection found under {base!r}. Pass the path as an argument.")
    listing = "\n  ".join(hits)
    sys.exit(f"Multiple profiles found — pass one as an argument:\n  {listing}")


def _fmt(sv):
    return "ABSTAIN" if sv.abstained else str(int(sv.score))


def main():
    path = _find_collection()
    print(f"collection: {path}")
    try:
        col = Collection(path)  # raises if the app has it open/locked
    except Exception as exc:
        sys.exit(f"Could not open the collection (is the desktop app still open?): {exc!r}")
    try:
        # Clean slate for the GMAT deck only, so the seed isn't double-counted.
        col.db.execute(
            "delete from revlog where cid in (select c.id from cards c "
            "join decks d on d.id = c.did where d.name like ?)",
            PARENT + "%",
        )
        col.db.execute(
            "update cards set type=0, queue=0, ivl=0, reps=0, lapses=0, data='{}' "
            "where did in (select id from decks where name like ?)",
            PARENT + "%",
        )
        col.save()

        n = seed_demo_history(col)

        parent = col.decks.by_name(PARENT)
        if parent is not None:
            parent["collapsed"] = False
            parent["browserCollapsed"] = False
            col.decks.save(parent)

        col.save()
        s = col._backend.get_gmat_scores(PARENT)
        print(f"seeded {n} reviews -> memory={_fmt(s.memory)} performance={_fmt(s.performance)} "
              f"readiness={_fmt(s.readiness)}/{s.readiness.confidence}")
        if n == 0:
            print("NOTE: no subdecks to seed — launch the app once (it organises the "
                  "deck into the 28 topics), then re-run this.")
        else:
            print("Done. Reopen the desktop app to see the mid-progress state.")
    finally:
        col.close()


if __name__ == "__main__":
    main()
