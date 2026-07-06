#!/usr/bin/env python3
"""Split the single GMAT Focus deck into per-topic subdecks + a full-exam parent.

Every card in the packaged deck (``content/gmat_focus.apkg``) carries a
``Section::Topic::Subtopic`` topic tag (the T1 tag contract, see
``content/taxonomy.md``). This tool re-files every card into a subdeck named::

    GMAT Focus::<Topic label>

where ``<Topic label>`` is the SAME human-readable label the readiness coverage
map uses (``anki.gmat_readiness._prettify_topic``), e.g.::

    Quant::Arithmetic::Percents            -> GMAT Focus::Arithmetic · Percents
    Verbal::CriticalReasoning::Assumption  -> GMAT Focus::Critical Reasoning · Assumption
    DataInsights::DataSufficiency          -> GMAT Focus::Data Sufficiency

Using the coverage-map label (not a re-invented name) means the deck tree lines
up 1:1 with the §7c coverage map / readiness topics, so the desktop and iOS UIs
can show one row per topic that matches the score dashboard.

The parent ``GMAT Focus`` deck holds NO cards directly; studying it studies all
subdecks, i.e. the full exam (all topics). Practising one topic = studying its
subdeck.

A card whose topic tag does not map to one of the 28 canonical outline topics is
logged and filed under ``GMAT Focus::Uncategorized`` (never dropped). With the
current seed this set is empty.

Only DECK ASSIGNMENT changes. Card content, tags, note types, scheduling and
media are left untouched. The result is written (byte-identical) to BOTH
``content/gmat_focus.apkg`` and the bundled iOS copy, and then re-opened to
verify the deck tree, the 108-card total, and that it imports cleanly.

Run:  anki/out/pyenv/bin/python content/tools/split_decks.py
        [--check-only]   verify existing apkgs without rebuilding
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
import tempfile
import zipfile
from collections import Counter

# --------------------------------------------------------------------------
# Paths. Everything is anchored at the repo root so the tool is CWD-independent
# (agent bash threads reset CWD between calls).
# --------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ANKI_ROOT = os.path.join(REPO_ROOT, "anki")

# The `anki` python lib is a namespace package split across pylib/ (hand-written)
# and out/pylib/ (generated protobuf + rust bridge). Both must be importable.
sys.path.insert(0, os.path.join(ANKI_ROOT, "pylib"))
sys.path.insert(0, os.path.join(ANKI_ROOT, "out", "pylib"))

from anki.collection import (  # noqa: E402
    Collection,
    ExportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)
from anki.gmat_readiness import (  # noqa: E402
    _all_outline_topics,
    _prettify_topic,
    covered_outline_tag_from_tags,
)

APKG_CANONICAL = os.path.join(REPO_ROOT, "content", "gmat_focus.apkg")
APKG_IOS = os.path.join(
    REPO_ROOT,
    "ios",
    "AnkiBridgeStub",
    "AnkiBridgeStub",
    "gmat_focus.apkg",
)

PARENT_DECK = "GMAT Focus"
UNCATEGORIZED = f"{PARENT_DECK}::Uncategorized"

#: Precompute the canonical outline-tag -> deck-name map once. The deck label is
#: exactly the coverage-map label so topics line up with the readiness §7c map.
TOPIC_DECK: dict[str, str] = {
    tag: f"{PARENT_DECK}::{_prettify_topic(tag)}" for tag in _all_outline_topics()
}


# --------------------------------------------------------------------------
# apkg (un)packing helpers. gmat_focus.apkg is a *legacy* package: a zip
# containing an inner `collection.anki2` (schema 11) plus a `media` map file
# (`{}` here -- no media blobs). We open the inner collection with the anki lib,
# re-file the cards, then re-export a legacy package so the format is unchanged.
# --------------------------------------------------------------------------


def _extract_apkg(apkg_path: str, dest_dir: str) -> str:
    """Unzip an apkg into dest_dir and return the inner collection path.

    Prefer the newest schema present. A *legacy* export (what we write) contains
    both a real ``collection.anki21`` AND a near-empty ``collection.anki2``
    backward-compat stub; we must read the ``.anki21`` (the stub holds a single
    placeholder card). ``.anki21b`` (zstd-compressed) would be preferred if
    present, but the anki lib opens only plain-sqlite paths, so we return
    ``.anki21``/``.anki2`` for opening and never a compressed db.
    """
    with zipfile.ZipFile(apkg_path) as zf:
        zf.extractall(dest_dir)
    # newest usable (plain-sqlite) schema first
    for name in ("collection.anki21", "collection.anki2"):
        p = os.path.join(dest_dir, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no inner collection found inside {apkg_path}")


def open_collection(inner_db: str) -> Collection:
    return Collection(inner_db)


# --------------------------------------------------------------------------
# The split itself.
# --------------------------------------------------------------------------


def _ensure_v3_scheduler(col: Collection) -> None:
    """The backend `set_deck` op requires the v3 scheduler; the legacy package
    ships with the v1/v2 scheduler. Upgrade in-memory (v1 -> v2 -> v3). This only
    flips the scheduler config flag -- it does NOT alter card content, tags or
    note types -- and the package is still exported in legacy format afterwards.
    """
    if col.v3_scheduler():
        return
    if col.sched_ver() != 2:
        col.upgrade_to_v2_scheduler()
    col.set_v3_scheduler(True)


def split_into_topic_decks(col: Collection) -> dict:
    """Re-file every card into GMAT Focus::<Topic>. Returns a report dict."""
    _ensure_v3_scheduler(col)

    # Remember which decks held cards before, so we can clean up any that are
    # left empty (e.g. the old "Exam Items" / "Memory" subdecks).
    original_dids = {
        did for (did,) in col.db.execute("select distinct did from cards")
    }

    # Bucket every card by its destination deck name.
    dest_to_cards: dict[str, list[int]] = {}
    per_topic = Counter()
    uncategorized: list[tuple[int, str]] = []

    for (cid,) in col.db.execute("select id from cards"):
        card = col.get_card(cid)
        tags = card.note().tags  # list[str], content untouched
        outline = covered_outline_tag_from_tags(tags)
        if outline is None:
            deck_name = UNCATEGORIZED
            uncategorized.append((cid, " ".join(tags)))
        else:
            deck_name = TOPIC_DECK[outline]
            per_topic[outline] += 1
        dest_to_cards.setdefault(deck_name, []).append(cid)

    # Create the destination decks and move the cards. col.decks.id(name)
    # creates the deck (and its parent "GMAT Focus") if absent, else returns it.
    moved = 0
    for deck_name, cids in sorted(dest_to_cards.items()):
        did = col.decks.id(deck_name)
        col.set_deck(cids, did)
        moved += len(cids)

    # Ensure the parent deck exists explicitly (it will already, via the
    # children, but be defensive) and holds no cards directly.
    parent_did = col.decks.id(PARENT_DECK)

    # Remove any deck that used to hold cards but is now empty (old subdecks),
    # so the topic list the UI shows is clean. Never remove the parent or a
    # freshly-created topic deck.
    keep_dids = {col.decks.id(n) for n in dest_to_cards} | {parent_did}
    removed_decks: list[str] = []
    for did in original_dids:
        if did in keep_dids:
            continue
        deck = col.decks.get(did, default=False)
        if deck is None:
            continue
        name = deck["name"]
        # only prune emptied GMAT Focus:: subdecks; leave Default etc. alone
        if not name.startswith(f"{PARENT_DECK}::"):
            continue
        if col.decks.card_count([did], include_subdecks=False) == 0:
            col.decks.remove([did])
            removed_decks.append(name)

    return {
        "moved": moved,
        "per_topic": per_topic,
        "uncategorized": uncategorized,
        "removed_decks": removed_decks,
    }


# --------------------------------------------------------------------------
# Export / verify.
# --------------------------------------------------------------------------


def export_legacy_apkg(col: Collection, out_path: str) -> None:
    """Export the whole collection as a legacy .apkg (matches the input format,
    scheduling + media preserved)."""
    options = ExportAnkiPackageOptions(
        with_scheduling=True,
        with_deck_configs=True,
        with_media=True,
        legacy=True,  # keep the legacy collection.anki2 format the input used
    )
    # limit=None -> whole collection.
    col.export_anki_package(out_path=out_path, options=options, limit=None)


def deck_tree_report(inner_db: str) -> tuple[list[tuple[str, int]], int]:
    """Re-open an inner collection and return (sorted [(deck_name, card_count)],
    total_cards). Counts are per-deck EXCLUDING subdecks so they sum to total."""
    col = open_collection(inner_db)
    try:
        rows: list[tuple[str, int]] = []
        for deck in col.decks.all_names_and_ids(skip_empty_default=False):
            name = deck.name
            did = deck.id
            n = col.decks.card_count([did], include_subdecks=False)
            rows.append((name, n))
        total = col.db.scalar("select count() from cards")
    finally:
        col.close()
    rows.sort(key=lambda r: r[0])
    return rows, total


def verify_imports(apkg_path: str) -> tuple[bool, str]:
    """Prove the package imports cleanly into a fresh collection."""
    tmp = tempfile.mkdtemp(prefix="split_verify_import_")
    try:
        fresh_db = os.path.join(tmp, "fresh.anki2")
        col = Collection(fresh_db)
        try:
            req = ImportAnkiPackageRequest(package_path=apkg_path)
            log = col.import_anki_package(req)
            # count cards that came in
            imported_cards = col.db.scalar("select count() from cards")
            found = log.log.found_notes
            return True, f"imported OK: found_notes={found}, cards={imported_cards}"
        finally:
            col.close()
    except Exception as e:  # noqa: BLE001 - report any import failure
        return False, f"import FAILED: {e!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def print_report(rows: list[tuple[str, int]], total: int) -> None:
    print("\nDeck tree (per-deck card counts, excluding subdecks):")
    parent_total = 0
    for name, n in rows:
        depth = name.count("::")
        indent = "  " * depth
        base = name.split("::")[-1] if "::" in name else name
        print(f"  {indent}{base:45s} {n:>4d}")
        if name == PARENT_DECK or name.startswith(f"{PARENT_DECK}::"):
            parent_total += n
    print(f"\n  GMAT Focus subtree cards: {parent_total}")
    print(f"  TOTAL cards in collection: {total}")


# --------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------


def build() -> int:
    if not os.path.exists(APKG_CANONICAL):
        print(f"ERROR: {APKG_CANONICAL} not found", file=sys.stderr)
        return 2

    work = tempfile.mkdtemp(prefix="split_decks_")
    try:
        inner_db = _extract_apkg(APKG_CANONICAL, work)
        col = open_collection(inner_db)
        before_total = col.db.scalar("select count() from cards")
        print(f"Opened source collection: {before_total} cards")

        report = split_into_topic_decks(col)
        after_total = col.db.scalar("select count() from cards")

        print(f"Moved {report['moved']} cards into GMAT Focus:: subdecks.")
        if report["removed_decks"]:
            print(f"Removed emptied subdecks: {report['removed_decks']}")
        if report["uncategorized"]:
            print(f"UNCATEGORIZED ({len(report['uncategorized'])}) -> {UNCATEGORIZED}:")
            for cid, tags in report["uncategorized"]:
                print(f"    card {cid}: tags={tags!r}")
        else:
            print("Uncategorized cards: 0 (every card mapped to a canonical topic).")

        assert after_total == before_total == 108, (
            f"card count changed! before={before_total} after={after_total}"
        )

        # Export to a staging file, then copy to both destinations so the bytes
        # are guaranteed identical (single export, two copies).
        staged = os.path.join(work, "gmat_focus.apkg")
        export_legacy_apkg(col, staged)
        col.close()

        shutil.copyfile(staged, APKG_CANONICAL)
        shutil.copyfile(staged, APKG_IOS)
        print(f"\nWrote:\n  {APKG_CANONICAL}\n  {APKG_IOS}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return verify()


def verify() -> int:
    print("\n" + "=" * 64)
    print("VERIFY")
    print("=" * 64)

    ok = True

    # 1. byte-identical
    identical = filecmp.cmp(APKG_CANONICAL, APKG_IOS, shallow=False)
    print(f"apkg files byte-identical: {identical}")
    print(f"  {APKG_CANONICAL} ({os.path.getsize(APKG_CANONICAL)} bytes)")
    print(f"  {APKG_IOS} ({os.path.getsize(APKG_IOS)} bytes)")
    ok = ok and identical

    # 2. re-open + deck tree + total
    work = tempfile.mkdtemp(prefix="split_verify_")
    try:
        inner_db = _extract_apkg(APKG_CANONICAL, work)
        rows, total = deck_tree_report(inner_db)
        print_report(rows, total)
        if total != 108:
            print(f"  ERROR: expected 108 cards, got {total}")
            ok = False
        # parent must hold no cards directly
        parent_direct = dict(rows).get(PARENT_DECK, 0)
        if parent_direct != 0:
            print(f"  ERROR: parent '{PARENT_DECK}' holds {parent_direct} cards directly")
            ok = False
        else:
            print(f"  OK: parent '{PARENT_DECK}' holds 0 cards directly (studying it = full exam).")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 3. importable
    imp_ok, imp_msg = verify_imports(APKG_CANONICAL)
    print(f"import check: {imp_msg}")
    ok = ok and imp_ok

    print("\n" + ("VERIFY PASSED" if ok else "VERIFY FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="verify the existing apkgs without rebuilding",
    )
    args = ap.parse_args()
    return verify() if args.check_only else build()


if __name__ == "__main__":
    raise SystemExit(main())
