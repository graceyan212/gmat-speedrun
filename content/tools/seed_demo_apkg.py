#!/usr/bin/env python3
"""Regenerate content/gmat_focus.apkg with baked-in mid-progress demo history.

Imports the current deck, seeds review history via ``anki.gmat_demo_seed``, and
re-exports the whole collection (with scheduling + media) so BOTH the desktop
app and the iPhone app import an identical "midway through practising" state:
the three shared-engine scores populate and the topic-coverage map shows real
progress, instead of a blank from-scratch deck.

Re-runnable and self-verifying: seeding is idempotent (skips if history is
already present), and the script imports the freshly exported apkg into a clean
collection and asserts all three scores are non-abstaining and all 28 subdecks
are present BEFORE it overwrites the shipped apkg.

Usage:  python content/tools/seed_demo_apkg.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # .../speedrun
ANKI = os.path.join(REPO, "anki")

# Re-exec under Anki's bundled Python (it has anki's deps, e.g. `markdown`).
_PYENV = os.path.join(ANKI, "out", "pyenv", "bin", "python")
if os.path.exists(_PYENV) and os.path.realpath(sys.executable) != os.path.realpath(_PYENV):
    os.execv(_PYENV, [_PYENV, os.path.abspath(__file__), *sys.argv[1:]])

sys.path[:0] = [os.path.join(ANKI, "out", "pylib"), os.path.join(ANKI, "pylib")]

import anki.generic_pb2 as generic  # noqa: E402
import anki.import_export_pb2 as ie  # noqa: E402
import anki.lang  # noqa: E402

anki.lang.set_lang("en")
from anki.collection import Collection  # noqa: E402
from anki.gmat_demo_seed import seed_demo_history  # noqa: E402

APKG = os.path.join(REPO, "content", "gmat_focus.apkg")
IOS_APKG = os.path.join(REPO, "ios", "AnkiBridgeStub", "AnkiBridgeStub", "gmat_focus.apkg")
PARENT = "GMAT Focus"


def _import(col, path, scheduling):
    col._backend.import_anki_package(
        package_path=path,
        options=ie.ImportAnkiPackageOptions(with_scheduling=scheduling),
    )


def _scores(col):
    s = col._backend.get_gmat_scores(PARENT)
    ok = not (s.memory.abstained or s.performance.abstained or s.readiness.abstained)
    return s, ok


def main():
    # 1. import the current deck and seed mid-progress history
    col = Collection(os.path.join(tempfile.mkdtemp(), "c.anki2"))
    _import(col, APKG, True)
    n = seed_demo_history(col)
    s, ok = _scores(col)
    print(f"seeded {n} reviews -> mem={s.memory.score:.0f} perf={s.performance.score:.0f} "
          f"readiness={s.readiness.score:.0f}/{s.readiness.confidence} ok={ok}")
    assert ok, "seed did not clear all three abstain thresholds"

    # 2. export the whole collection with scheduling + media (modern format)
    out = os.path.join(tempfile.mkdtemp(), "seeded.apkg")
    col.export_anki_package(
        out_path=out,
        options=ie.ExportAnkiPackageOptions(
            with_scheduling=True, with_deck_configs=True, with_media=True, legacy=False
        ),
        limit=ie.ExportLimit(whole_collection=generic.Empty()),
    )
    col.close()

    # 3. verify the production path: fresh reimport WITH scheduling shows all
    #    three scores and all 28 subdecks
    v = Collection(os.path.join(tempfile.mkdtemp(), "v.anki2"))
    _import(v, out, True)
    s2, ok2 = _scores(v)
    subdecks = sum(1 for d in v.decks.all_names_and_ids() if d.name.startswith(PARENT + "::"))
    reviews = v.db.scalar("select count(*) from revlog")
    print(f"reimport -> mem={s2.memory.score:.0f} perf={s2.performance.score:.0f} "
          f"readiness={s2.readiness.score:.0f}/{s2.readiness.confidence} ok={ok2} "
          f"subdecks={subdecks} revlog={reviews}")
    v.close()
    assert ok2 and subdecks == 28, "reimport verification failed"

    # 4. ship it to both the desktop content deck and the iOS bundle
    shutil.copyfile(out, APKG)
    shutil.copyfile(out, IOS_APKG)
    print(f"updated {APKG}")
    print(f"updated {IOS_APKG}")


if __name__ == "__main__":
    main()
