# Sync test: no reviews lost, none double-counted, and a clear conflict winner

**Rubric 7b.** *Review 10 cards offline on device A and 10 different cards
offline on device B, reconnect, and show all 20 land in one place with none lost
and none double-counted. Then review the **same** card on both devices offline,
sync, and show the conflict rule picks a clear correct winner — with the rule
written down.*

**Headline result (from the run):**

| Check | Expected | Observed |
|---|:---:|:---:|
| Reviews that land after sync (device A) | 20 | **20** |
| Reviews that land after sync (device B) | 20 | **20** |
| Distinct cards reviewed (A / B) | 20 / 20 | **20 / 20** |
| Reviews **lost** | 0 | **0** |
| Reviews **double-counted** | 0 | **0** |
| A and B converge to the same state | yes | **yes** |
| Conflict winner (same card, A=Again vs B=Easy) | deterministic | **B (Easy), later modification time** |
| Both conflicting reviews retained in history | yes | **yes** |

Both parts pass; the test process exits `0`. Every number above is printed by
[`content/scripts/sync_conflict_test.py`](../content/scripts/sync_conflict_test.py)
— see [Reproduce](#reproduce).

---

## How this uses the real sync stack (not a stand-in)

This is not a hand-rolled merge. It exercises **Anki's own sync client and
server**, which is exactly what the shipping apps use (design:
[`docs/superpowers/specs/2026-07-02-ios-desktop-sync-design.md`](superpowers/specs/2026-07-02-ios-desktop-sync-design.md)).
The two devices are two independent local collections; the hub is the fork's
own sync server.

- **Server:** rslib's built-in sync server, started via `python -m anki.syncserver`
  (which calls `RustBackend.syncserver()` — the prebuilt engine, **no `cargo`
  build**). It is launched as a **separate process** on a temporary port with a
  temporary `SYNC_BASE` data dir and `SYNC_USER1=demo:gmatsync2026`. (It must be a
  separate process, not an in-thread server: `syncserver()` runs a blocking
  runtime that holds the Python GIL, which would starve the sync-client calls.)
- **Clients:** two throwaway collections (`A`, `B`), each seeded from the same
  deck [`content/gmat_focus.apkg`](../content/gmat_focus.apkg), using the same
  client calls the apps use — `sync_login` → `sync_collection` → (on full-sync
  signal) `full_upload_or_download`. This is the same API proven by the earlier
  one-note round-trip smoketest,
  [`content/scripts/sync_smoketest.py`](../content/scripts/sync_smoketest.py).
- **Isolation:** the test **never touches your real collection** and **never
  contacts the fly server** (`gmat-sync.fly.dev`). Everything is on
  `127.0.0.1`, on a random free port, in temp dirs that are discarded.

### Common base first

First sync is ordered (spec §6): A seeds the empty server (a **FULL_UPLOAD** of
the 108-card deck), then B does a **FULL_DOWNLOAD** to receive it. After that,
A and B hold the identical base (108 cards each) and sync incrementally in either
direction — this is the state a real desktop+phone pair are in after their first
sync.

---

## Part 1 — 10 + 10 offline → 20 land, none lost, none double-counted

**Setup.** From the shared 108-card base, pick 21 distinct cards up front: 10
for A, 10 for B (disjoint), and 1 shared card reserved for Part 2.

**Offline (no sync between these two steps):**

- Device **A** answers its **10** cards (rating *Good*).
- Device **B** answers its **10** *different* cards (rating *Hard*).

Each side locally holds exactly its own 10 reviews (`A local revlog = 10`,
`B local revlog = 10`).

**Reconnect and converge.** Sync A → server, then B → server, then A again,
until both report no further changes.

**Assertion (the actual printed numbers):**

```
=== PART 1 RESULT (after convergence) ===
  revlog rows for the 20 cards on A: 20
  revlog rows for the 20 cards on B: 20
  distinct cards reviewed on A:      20
  distinct cards reviewed on B:      20
  -> 20 land: True | 0 lost: True | 0 dup: True | A==B: True
  PART 1 PASS
```

**Reading the result:** the revlog on **both** A and B contains **exactly 20**
rows across the 20 cards, spread over **20 distinct** cards. `20 == 20` on each
side means nothing was **duplicated**; `20 distinct` means nothing was **lost**;
`A == B` means both devices converged to the identical set. All 20 reviews landed
in one place, once each.

> Note: `sync_collection()` returning `NO_CHANGES` in the logs means "no *full*
> up/download required" — the normal incremental delta is applied inside the
> call itself. The proof that data moved is the revlog counts going from 10 → 20
> on each side, not the return code.

---

## Part 2 — same card on both devices → one clear winner

**Setup.** Both devices answer the **same** reserved card, offline, with
**different** ratings, a couple of seconds apart:

- Device **A** answers **Again** (ease 1) first.
- Device **B** answers **Easy** (ease 4) ~2 s later.

Then sync A, sync B, and converge.

**Observed outcome (the actual printed result):**

```
=== PART 2: conflict on one shared card ===
  A answered ease=1 (Again) @ 1783302429603, new revlog: [[1783302429603, 1]]
  B answered ease=4 (Easy)  @ 1783302431608, new revlog: [[1783302431608, 4]]
  A final revlog for conflict card: [[1783302429603, 1], [1783302431608, 4]]
  B final revlog for conflict card: [[1783302429603, 1], [1783302431608, 4]]
  A card row (mod,queue,type,ivl,due): [1783302426, 2, 2, 3, 3]
  B card row (mod,queue,type,ivl,due): [1783302426, 2, 2, 3, 3]
  A and B converged identically (card row + revlog): True
  both reviews kept in revlog (no double-count, no loss): True
  CONFLICT WINNER (card's converged schedule): B's review (ease=4 Easy, later mod time)
```

**Winner: device B's *Easy* review.** After sync, **both** A and B show the
identical card row — `queue=2, type=2` (a review card) with `ivl=3` — which is
the *Easy* outcome, not the *Again* outcome. B's review won because it was the
**later** write. At the same time, **both** reviews survive in the revlog
history on both devices (`[[…, 1], […, 4]]`): the *Again* is not erased, it is
just superseded for the card's forward schedule. Nothing was lost and nothing was
counted twice.

### The conflict rule (written down)

Quoting the design spec,
[`docs/superpowers/specs/2026-07-02-ios-desktop-sync-design.md`](superpowers/specs/2026-07-02-ios-desktop-sync-design.md)
§4.4 — inherited directly from Anki, not invented here:

> - Normal changes merge via the revlog + change-tracking: both devices' reviews
>   are applied, nothing is double-counted.
> - On true divergence (can't reconcile incrementally — schema change or a failed
>   sanity check), Anki forces a **full upload/download**: the side that uploads
>   becomes authoritative and the other matches it.

Concretely, as this test observes it:

1. **Review history (`revlog`) merges losslessly.** Each review is a row keyed by
   its own millisecond timestamp id, so every review from every device is kept.
   This is why 10 + 10 → 20 with no loss and no duplication, and why *both* the
   *Again* and *Easy* rows remain after the conflict.
2. **A shared object that both sides edited (the card row) resolves
   last-write-wins by modification time.** The card is a single row keyed by card
   id; the sync keeps the copy with the newer `mod` timestamp. B answered later,
   so B's *Easy* result is the one both devices converge to. This is Anki's
   normal per-object reconciliation.
3. **Genuine divergence falls back to full up/download**, and the uploading side
   is authoritative (§4.4). This is the safety net for cases that cannot be
   reconciled incrementally; the everyday review-vs-review case is handled by
   (1) and (2) above, which is what the 7b scenario is.

**Why the timings in the test are spaced deliberately.** Two reviews in the exact
same millisecond would share a revlog id, and two edits to the same card in the
same *second* would share a `mod` time — both are artifacts of a test that fires
faster than any human. The script gives each review a distinct millisecond id and
puts the two conflicting card edits in different seconds, which is what naturally
happens when a person reviews on two real devices minutes apart. The merge logic
itself is unchanged; the spacing only removes synthetic ties so the winner is
unambiguous and the run is deterministic.

---

## Reproduce

Prerequisite: the engine is built (`anki/out/pylib` + the pyenv exist — the same
build the desktop app uses). Then, from the repo root:

```bash
PYTHONPATH=anki/out/pylib:anki/out/qt \
  anki/out/pyenv/bin/python content/scripts/sync_conflict_test.py
```

The script starts its own local sync server, seeds two temp collections, runs
both parts, prints the numbers above, and exits `0` on success. It cleans up its
temp server and collections; nothing is left in your real collection or on the
fly server.

Related:
[`content/scripts/sync_smoketest.py`](../content/scripts/sync_smoketest.py) is the
minimal one-note A→server→B round-trip that de-risked the sync API this test
builds on.
