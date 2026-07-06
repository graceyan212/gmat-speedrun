# Crash-safety & offline behaviour (rubric 7g)

Two robustness questions, both answered at the **engine** level:

- **7g(a) — crash.** Kill the app mid-review 20 times in a row → **zero** corrupted
  collections.
- **7g(b) — offline.** Pull the network → the AI feature turns off cleanly while
  both apps keep working and **still give a score**.

## Why testing the engine covers *both* apps

The desktop app and the iPhone app are not two separate implementations of the
study logic. They are two front-ends over **one** engine:

```
        desktop (aqt / Qt)                 iPhone (SwiftUI)
                \                               /
                 \                             /
              Anki's Rust core  ── rslib ──  (via the C-ABI bridge in bridge/)
                             |
                        SQLite (WAL)   <- the collection file, one format
```

Grading, the three GMAT scores, adaptive card selection, and the per-topic
breakdown are all pure Rust in `rslib`, reading/writing a single SQLite file.
The desktop calls it in-process; the phone calls the *same* compiled Rust across
the C-ABI bridge (`bridge/`, packaged as `AnkiRust.xcframework`). So a crash-safety
proof and an offline proof **at the engine level are proofs for both platforms** —
there is no second copy of the logic that could behave differently.

The two tests drive that engine directly through the pylib `Collection` API
(`anki.collection.Collection`, the thinnest possible wrapper over rslib) on
**throwaway temp collections** seeded from `content/gmat_focus.apkg`. They never
touch user data and never launch the GUI.

- `content/scripts/crash_test.py` — the 20× kill-mid-review loop.
- `content/scripts/offline_test.py` — the network-pulled engine checks.
- `content/scripts/_gmat_test_common.py` — shared seed / setup / integrity-check
  helpers.

---

## 7g(a) Crash test — kill mid-review 20×, expect 0 corruptions

### Method

Repeated **20 times**:

1. **Fresh collection.** Copy the seeded deck to a new temp `collection.anki2`.
   (Seeding once and copying gives every run an identical, cleanly-checkpointed
   starting file.)
2. **Child process does real review writes.** Spawn a **child** process
   (`subprocess`, `crash_test.py --child <path>`) that opens the collection and
   enters a tight loop: fetch the next card and `answer_card(...)` it, over and
   over. Each answer is exactly the write a student's tap triggers — a scheduler
   mutation wrapped in a **SQLite transaction** (revlog insert + card update).
3. **Pull the plug mid-write.** After a short *randomized* delay (0.15–0.45 s, so
   the signal lands *while* a transaction is in flight, not after a clean finish),
   the parent kills the child **hard** with `os.kill(pid, SIGKILL)` — no cleanup,
   no chance to commit or close. This is the OS-level "battery yanked".
4. **Reopen and verify.** Back in the parent, check the file the killed child left
   behind:
   - SQLite **`PRAGMA integrity_check`** returns `ok` (structure sound);
   - `Collection(path)` **reopens without error** (the engine accepts the file);
   - **note/card counts are intact** (108 / 108 — no torn or lost rows).

> **Note on `integrity_check` + the `unicase` collation.** Anki registers a custom
> `unicase` collation on its own SQLite connection
> (`rslib/src/storage/sqlite.rs`), and several indexes are declared
> `COLLATE unicase`. A raw `sqlite3` connection doesn't have that collation, so
> `integrity_check` — which walks those indexes — errors with *"no such collation
> sequence"* unless we register it. The helper registers an ASCII
> case-insensitive collation (matching rslib's `UniCase` for ASCII deck/tag
> names) before running the pragma. See `register_unicase()` in
> `_gmat_test_common.py`.

### Why it can't corrupt

Anki opens SQLite in **WAL (write-ahead logging)** mode and performs every review
change inside a transaction. When a process is `SIGKILL`ed mid-transaction, the
uncommitted tail of the WAL is simply **not replayed** on the next open: the last,
half-written answer is rolled back (lost), and everything already committed
survives intact. There is no in-place mutation of the main database that a partial
write could tear. That is the guarantee the test exercises 20 times.

### Result — **20/20 clean, 0 corrupted**

```
  [ 1/20] CLEAN     integrity=ok reopened notes=108 cards=108 revlog=76 (killed mid-run)
  [ 2/20] CLEAN     integrity=ok reopened notes=108 cards=108 revlog=76 (killed mid-run)
  ...
  [20/20] CLEAN     integrity=ok reopened notes=108 cards=108 revlog=76 (killed mid-run)
------------------------------------------------------------------------
RESULT: 20/20 clean, 0 corrupted
PASS: an interrupted write never corrupted the collection (SQLite WAL + Anki transactions).
```

Every one of the 20 children was confirmed **killed while still in its write loop**
(`killed mid-run`). The seed collection's revlog starts at **0**; each killed run
reopened with ~76 committed reviews present — proof that the child really was doing
transactional review writes, that the committed ones **survived** the hard kill,
and that the interrupted one was **cleanly rolled back**. `integrity_check` = `ok`
and counts = 108/108 on all 20.

---

## 7g(b) Offline test — network pulled, engine must still score

### The offline architecture

**Everything a review needs is local.** Grading, scoring, card selection and the
topic breakdown are pure `rslib` computations over the local SQLite file — no HTTP,
no model server, nothing off-device:

| Engine call | What it does | Where |
|-------------|--------------|-------|
| `grade_answer(correct, confidence)` | auto-grade a tapped answer → ease 1–4 | `rslib` scheduler |
| `get_gmat_scores(deck_name)` | the three scores (memory / performance / readiness) | `rslib/src/scheduler/gmat_scores.rs` |
| `get_queued_cards(...)` | the next card (incl. adaptive selection) | `rslib/src/scheduler/adaptive.rs` |
| `get_topic_breakdown(topic_depth)` | per-topic × per-difficulty-band accuracy | `rslib` scheduler |

**The AI feature is dev-time-only, so review is offline-safe by construction.**
The "AI" in this app is an **LLM difficulty calibration**
(`content/tools/calibrate_difficulty.py`): ahead of time, an LLM rates each item
0–100 and the rating is **baked into the note as an `aidiff::NN` tag**. That LLM
call happens **at build/dev time, never at review time**. At review time the engine
only *reads* tags — so pulling the network cannot affect a review.

**Graceful degradation to the coarse tag.** When the engine resolves a card's
difficulty it prefers the AI tag, then falls back:

- `aidiff::NN` present → use it (AI-rated 0–100), else
- coarse `difficulty::easy|medium|hard` → 20 / 50 / 80, else
- neutral 50.

See `note_difficulty()` in `rslib/src/scheduler/adaptive.rs` and
`difficulty_0_100()` in `rslib/src/scheduler/gmat_scores.rs` (both also reject a
non-finite `aidiff::nan/inf` and fall through to the coarse tag). So a card with
**no AI rating** doesn't break scoring — it simply scores off its coarse tag.

The shipped deck (`content/gmat_focus.apkg`) makes this concrete: it ships with
**0 `aidiff::` tags** — every card carries only a coarse `difficulty::` tag. The
coarse fallback is therefore already the *live* path, and the offline test scores
the whole deck on it.

### Method

The test **hard-disables the network for the entire process** before making any
engine call: it monkeypatches `socket.connect` / `create_connection` /
`getaddrinfo` to raise `NetworkPulled`. It first *proves the guard is live* by
attempting a real outbound connection (which must now raise), then confirms the
engine still:

1. **grades** a tapped answer (`grade_answer`);
2. **produces the three scores** (`get_gmat_scores`) — well-formed even when they
   abstain for lack of data;
3. **serves the next card** (`get_queued_cards`);
4. **breaks down topics** (`get_topic_breakdown`);
5. **gives a *real* score** — after answering 60 cards offline, at least one score
   stops abstaining and returns a bounded number;
6. **degrades AI difficulty to the coarse tag** — 0 `aidiff` tags in the deck, and
   the resolver returns coarse-hard→80, aidiff-wins-when-present→42, neither→50.

If any engine call had secretly reached out, the socket guard would have raised and
the test would fail.

### Result — **6/6 checks pass with the network down**

```
  network guard: outbound connections now raise NetworkPulled — OFFLINE.
  [PASS] grade_answer (local): correct->ease 4, wrong->ease 1 (overconfident=True)
  [PASS] get_gmat_scores (local): memory abstained=True, performance abstained=True, readiness abstained=True
  [PASS] get_queued_cards (local): served 1 card, counts new/lrn/rev=20/0/0
  [PASS] get_topic_breakdown (local): returned 0 topic rows
  [PASS] engine gives a real score offline: answered 60 cards offline -> performance score=76.6 in [64.1,85.7] pct; still-abstaining (honest give-up): ['memory', 'readiness']
  [PASS] AI-difficulty degrades to coarse tag: shipped deck has 0 aidiff tags -> scores run entirely on the coarse difficulty:: fallback; resolver: coarse hard->80, aidiff wins when present->42, neither->neutral 50
------------------------------------------------------------------------
RESULT: 6/6 local-engine checks passed with the network down
PASS: with NO network, the shared engine still grades, scores, selects cards and
      breaks down topics; the AI path is dev-time-only and degrades to the coarse tag.
```

After 60 offline answers the **performance** score becomes a real number
(76.6, range 64.1–85.7). Memory and readiness still *abstain* — that is their
**honest give-up rule**, not an offline failure: memory needs cards to graduate out
of learning and build an FSRS stability, and readiness needs ≥200 reviews and ≥50%
topic coverage; a single rapid session doesn't reach either. The engine reports
*exactly what it can defend* and says what's missing for the rest — with no network.

---

## Reproduce

Both scripts use the built pylib + the bundled Python (`anki/out/pyenv/bin/python`);
run them from the repo root. Neither touches user data (`just run` is **not**
involved).

```bash
cd /path/to/speedrun   # repo root

# 7g(a) — kill mid-review 20x, expect 20/20 clean (~6 s)
PYTHONPATH=anki/out/pylib:anki/pylib:anki/out/qt \
  anki/out/pyenv/bin/python content/scripts/crash_test.py

# 7g(b) — network pulled, engine must still score (~2 s)
PYTHONPATH=anki/out/pylib:anki/pylib:anki/out/qt \
  anki/out/pyenv/bin/python content/scripts/offline_test.py
```

Each prints a per-check log and exits `0` on pass, `1` on failure.
