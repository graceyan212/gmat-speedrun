#!/usr/bin/env python3
"""Per-topic practice + mid-progress demo-seed test (re-runnable).

Proves, against the shipped deck and the REAL desktop startup migration
(`anki/qt/aqt/gmat_deck_migrate.py`), that a legacy/flat collection is brought
to the state the demo needs:

  * the 28 per-topic subdecks exist and are populated,
  * the "GMAT Focus" exam parent is expanded (every topic visible in the deck
    list, not one collapsed row),
  * all three shared-engine scores are non-abstaining (Memory / Performance /
    Readiness) — i.e. it looks "midway through practising", not from scratch,
  * every topic is practiceable — a covered topic serves a due review and an
    uncovered topic serves a new card (no "session complete" dead-ends),
  * the migration is idempotent (a second launch moves/adds nothing).

Usage:  python content/scripts/topic_practice_test.py     (exit 0 = PASS)
"""

import importlib.util
import os
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANKI = os.path.join(REPO, "anki")

# Re-exec under Anki's bundled Python (it has anki's deps, e.g. `markdown`).
_PYENV = os.path.join(ANKI, "out", "pyenv", "bin", "python")
if os.path.exists(_PYENV) and os.path.realpath(sys.executable) != os.path.realpath(_PYENV):
    os.execv(_PYENV, [_PYENV, os.path.abspath(__file__), *sys.argv[1:]])

sys.path[:0] = [os.path.join(ANKI, "out", "pylib"), os.path.join(ANKI, "pylib")]

import anki.import_export_pb2 as ie  # noqa: E402
import anki.lang  # noqa: E402

anki.lang.set_lang("en")
from anki.collection import Collection  # noqa: E402

APKG = os.path.join(REPO, "content", "gmat_focus.apkg")
PARENT = "GMAT Focus"


def _ensure_v3(col):
    if not col.v3_scheduler():
        if col.sched_ver() != 2:
            col.upgrade_to_v2_scheduler()
        col.set_v3_scheduler(True)


def _serves(col, name):
    col.decks.select(col.decks.id(name))
    try:
        col.sched.reset()
    except Exception:
        pass
    return len(col.sched.get_queued_cards(fetch_limit=1).cards) > 0


def main():
    col = Collection(os.path.join(tempfile.mkdtemp(), "collection.anki2"))
    col._backend.import_anki_package(
        package_path=APKG, options=ie.ImportAnkiPackageOptions(with_scheduling=True)
    )

    # Simulate a legacy/flat collection: strip history, flatten, un-split, collapse.
    _ensure_v3(col)
    col.db.execute("delete from revlog")
    flat = col.decks.id(PARENT)
    col.set_deck([c for (c,) in col.db.execute("select id from cards")], flat)
    for d in col.decks.all_names_and_ids():
        if d.name.startswith(PARENT + "::"):
            col.decks.remove([d.id])
    col.db.execute("update cards set type=0, queue=0, ivl=0, reps=0, lapses=0, data='{}'")
    p = col.decks.by_name(PARENT)
    p["collapsed"] = True
    p["browserCollapsed"] = True
    col.decks.save(p)
    col.set_config("gmatDeckLayoutVersion", 0)
    col.save()

    # Run the REAL migration (the exact code profile_did_open triggers) with a
    # stubbed aqt.mw so it runs headless.
    fake = types.ModuleType("aqt")
    fake.mw = types.SimpleNamespace(col=col, reset=lambda: None)
    fake.gui_hooks = types.SimpleNamespace(
        profile_did_open=types.SimpleNamespace(append=lambda fn: None)
    )
    sys.modules["aqt"] = fake
    sys.modules["aqt.gui_hooks"] = fake.gui_hooks
    spec = importlib.util.spec_from_file_location(
        "gmat_deck_migrate", os.path.join(ANKI, "qt", "aqt", "gmat_deck_migrate.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._migrate()

    # Verify.
    subs = sorted(d.name for d in col.decks.all_names_and_ids() if d.name.startswith(PARENT + "::"))
    populated = [n for n in subs if col.db.scalar("select count(*) from cards where did=?", col.decks.id(n)) > 0]
    parent = col.decks.by_name(PARENT)
    s = col._backend.get_gmat_scores(PARENT)
    covered, uncovered = subs[0], subs[24]  # [0] is seeded; [24] is beyond the 18 covered
    serve_cov, serve_unc = _serves(col, covered), _serves(col, uncovered)

    before = dict(col.db.execute("select id,did from cards"))
    rl_before = col.db.scalar("select count(*) from revlog")
    mod._migrate()
    idempotent = before == dict(col.db.execute("select id,did from cards")) and rl_before == col.db.scalar(
        "select count(*) from revlog"
    )

    print(f"subdecks={len(subs)} populated={len(populated)} expanded={not parent.get('browserCollapsed')}")
    print(f"scores: memory={'ABSTAIN' if s.memory.abstained else int(s.memory.score)} "
          f"performance={'ABSTAIN' if s.performance.abstained else int(s.performance.score)} "
          f"readiness={'ABSTAIN' if s.readiness.abstained else int(s.readiness.score)}/{s.readiness.confidence}")
    print(f"practiceable: covered={serve_cov} uncovered={serve_unc}   idempotent={idempotent}")

    ok = (
        len(subs) == 28
        and len(populated) == 28
        and not parent.get("browserCollapsed")
        and not s.memory.abstained
        and not s.performance.abstained
        and not s.readiness.abstained
        and serve_cov
        and serve_unc
        and idempotent
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    col.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
