# Demo script — GMAT-on-Anki (desktop + phone, one Rust engine)

**One-line pitch:** *A GMAT Focus study app built on a fork of Anki, where the desktop
app and the iPhone app run the **same Rust engine** — so three honest scores, AI
confidence-grading, and adaptive selection are computed once in `rslib` and show up
**identically** on both, not as a Python bolt-on.*

**Total time: ~4 min spoken + ~1 min buffer.** Have both apps already open:
desktop Anki (fork) with the GMAT deck studied, and the iPhone simulator on the home
dashboard. Both should point at the *same collection* (synced) so the numbers match live.

---

## Biggest changes — the fallback list (lead top-to-bottom)

1. **Real shared-engine change in Rust (the headline).** The GMAT logic lives in Anki's
   core `rslib`, not in a plugin: three honest scores (`gmat_scores.rs`), per-topic mastery
   + points-at-stake reordering (`topic_mastery.rs`), computer-adaptive selection
   (`adaptive.rs`), confidence grading (`auto_grade.rs`). Desktop and iPhone both call the
   **same** RPCs (`GetGmatScores`, `GradeAnswer`, `GetTopicBreakdown`) through a thin C-ABI
   bridge → identical numbers on both.
2. **AI — confidence-based auto-grading.** Tap answer → *how sure* (Guessing / Fairly sure /
   Confident) → the engine turns *correctness × confidence* into Again/Hard/Good/Easy.
   Calibration, **not** speed. It flags the "confident-but-wrong" miss. (`grade_answer`.)
3. **AI — difficulty calibration + eval.** An LLM rates each item 0–100 with a cited reason
   (`aidiff::NN`), injection-hardened, feeds the Rasch difficulty `b`; evaluated held-out
   (Brier) against the coarse-tag baseline with a leakage-free split.
4. **Learning-science signals.** FSRS spaced repetition, weakest-topics-first ordering,
   abstain/give-up rules (a score refuses to show a number below enough evidence), and
   re-runnable held-out/paraphrase/ablation tests.
5. **UI/UX (lots of it, but secondary here).** Bauhaus design across phone + desktop, the
   28-topic coverage map (green only when a topic is actually practiced), the phone home
   dashboard, per-topic easy/medium/hard breakdown.

> Emphasis order for graders: **engine + AI + learning science first, UI last.**

---

## [0:00–0:30] Hook — one engine, two apps

**SAY:** "This is a GMAT Focus study app built on a fork of Anki. The interesting part isn't
that it's on a phone — it's that the phone and the desktop run the **same Rust engine**. The
desktop app is the Anki fork; the iPhone links Anki's `rslib` core through a small C-ABI
bridge. So the GMAT logic I'll show — the scores, the grading, the adaptive selection — is a
real engine change in Rust, computed once and shown identically on both. Not a Python add-on."

**SHOW:** Desktop Anki open next to the iPhone simulator. Gesture to both. Have the GMAT deck
visible on desktop and the home dashboard on the phone.

---

## [0:30–1:45] The real engine change — three honest scores

**SAY:** "The centerpiece is three scores, each answering a different question, each with a
range and its own give-up rule. **Memory** is FSRS recall — can you remember what you've
studied. **Performance** is a real Rasch / 1PL ability estimate from item-response theory —
solved with Newton's method, weighting each answer by how hard the item was — so getting hard
questions right counts for more. **Readiness** projects that ability onto the real GMAT Focus
**205–805** scale, discounted by how much of the exam you've actually covered. All three are
computed in one Rust function, `GetGmatScores`, so — watch — the desktop and the phone show the
**same numbers**."

**SHOW:**
- Desktop: **Tools ▸ GMAT Readiness** (or the Readiness link in the top toolbar). Point at the
  three scores, each with its range.
- Phone: tap the three-score chip / open the scores page. Show the *same* three numbers.
- Say: "Same engine, same collection, same math — the phone isn't recomputing anything, it's
  calling the same `rslib` RPC."

**SAY (the honesty beat — say it, don't skip it):** "And each score **abstains** when it can't
defend a number. Memory needs at least **30** reviews; Performance needs **20** answered with a
mix of right and wrong — an all-correct record can't pin an ability; Readiness is strictest —
**200** reviews **and** at least **50%** topic coverage. Below that it shows you exactly what's
missing instead of a fake number."

**SHOW:** If a score is abstaining, show the "missing" line (e.g. *"Answer at least 200 cards
(have N)"*). If all are populated, point at the Readiness confidence label (low/medium/high,
driven by coverage) and its range.

---

## [1:45–2:45] AI — confidence-based grading

**SAY:** "Here's the AI grading feature, and it's a deliberate design choice. When you answer,
you don't hit an ease button — you tap your choice, then say how sure you were: **Guessing,
Fairly sure, or Confident**. The engine turns *correctness × confidence* into the rating —
wrong is always Again; right is Hard, Good, or Easy by how sure you were. It grades on
**calibration, not speed** — time is confounded by reading and computation load, so the engine
never uses it to grade. And it flags the miss that hurts most on an adaptive test: **confident
but wrong.**"

**SHOW:**
- Phone (or desktop reviewer): answer a card — tap a choice, then tap a confidence level. Show
  the card resolve to a rating without any manual ease button.
- Deliberately get one wrong while marking "Confident" to trigger the **confident-but-wrong**
  flag; point it out.
- Mention: "Response time is still shown — but only for **pacing**, with a *Flag & skip* move
  to cut your losses. It never feeds the grade."

**SAY (tie it back to the engine):** "This is `grade_answer` in the shared engine — a pure,
offline function — so desktop and phone grade identically, and the manual ease buttons still
work as an override. It's on by default on both."

---

## [2:45–3:20] AI difficulty + learning-science signals

**SAY:** "Two more things feeding this. First, an **LLM rates every item's difficulty 0–100**
with a one-line cited reason, and that feeds the Rasch difficulty — replacing Anki's coarse
three-bucket tag. It's injection-hardened (the item text is treated as untrusted data) and
evaluated **held-out**: on a leakage-free, item-disjoint split it beats the coarse baseline on
Brier calibration — a small, honestly-reported edge. Second, reviews are reordered so your
**weakest topics come first**, and there's an optional **computer-adaptive mode** that serves
questions near your ability — off by default, and it doubles as our study-feature ablation."

**SHOW:** Optionally flash `docs/RESULTS.md` (the held-out Brier table) or `docs/scores/` — but
keep it brief; this is a "we measured it" beat, not a deep dive.

**SAY (the separation proof, if time):** "And we checked Performance isn't just echoing Memory:
a paraphrase test — same idea, new wording — shows recall on the memorized card at 86.8% vs
71.3% on the reworded questions, a **−15.5-point gap**. Memorizing a card isn't understanding
the idea, which is exactly why we keep them as separate scores."

---

## [3:20–3:50] UI tour — coverage map + Bauhaus

**SAY:** "Quickly on the interface — it's a cohesive **Bauhaus** design across phone and
desktop: Futura, primary palette, hard-edged flat controls. The one piece worth calling out is
the **coverage map**: the 28 GMAT Focus topics, and a square only turns **green when you've
actually practiced that topic**, so it can't overstate readiness. The phone home screen is a
deck picker with a per-topic easy/medium/hard breakdown."

**SHOW:** Phone home dashboard → the 28-topic coverage map (point at filled vs empty squares) →
tap a topic to expand its easy/medium/hard breakdown. Note the "TOPICS PRACTICED — N/28" line.
Tap a topic to show it serves a question from that subdeck.

---

## [3:50–4:00] Close

**SAY:** "So: one Rust engine, two apps, three honest scores that abstain when they can't
defend a number, AI grading on calibration, and every claim backed by a re-runnable test.
That's the build."

**SHOW:** Land on the desktop Readiness dashboard and the phone scores page side by side — the
same numbers — as the closing image.

---

## If asked — Q&A appendix

**Q: How is this a *real engine change* and not a Python add-on?**
The GMAT logic lives in Anki's core Rust `rslib` (`rslib/src/scheduler/gmat_scores.rs`,
`topic_mastery.rs`, `adaptive.rs`, `auto_grade.rs`), exposed as protobuf RPCs. **Both** apps
call the same RPCs — desktop through Anki's normal Python/Qt layer, the iPhone through a C-ABI
bridge (`anki_get_scores`, `anki_grade_answer`, `anki_get_topic_breakdown`). The phone does no
GMAT math of its own; it displays what the shared engine returns. There are ~36 Rust unit tests
across those four files.

**Q: How do you know the phone and desktop numbers actually match?**
They read the same collection through the same `GetGmatScores` code path — the phone links the
identical `rslib` compiled into the xcframework. Show both live side by side; that's the proof.

**Q: Why grade on confidence, not response time?**
Time is confounded — a hard reading-comprehension item legitimately takes longer than an easy
arithmetic one, and on an adaptive test item position skews it too. Calibration (knowing what
you know) is the trainable skill and the thing that sinks scores when it fails. So the engine
grades on *correctness × confidence* and only shows time for pacing. (See the docstring in
`auto_grade.rs`; it cites the calibration literature.)

**Q: The AI rates difficulty — how do you know it isn't leaking the answers / your own reviews?**
Difficulty comes from the **item text + rubric only** (`calibrate_difficulty.py`), with no
access to the revlog, so it can't encode held-out outcomes. The eval runs an explicit
train/test leakage check and reports the **item-disjoint** split (zero shared items between
train and held-out); AI still beats the coarse baseline there (Brier 0.1706 vs 0.1720). See
`docs/RESULTS.md` §2.1.

**Q: Is Performance just a re-skin of Memory?**
No — the paraphrase test (rubric 7d) measures the gap: recall on the memorized card 86.8% vs
accuracy on reworded questions 71.3%, a **−15.5-point** gap. Recalling a card ≠ answering the
idea in new words. It's a clearly-labelled simulation from the app's own Rasch model; the sign
is by design (a familiarity bonus), the magnitude falls out of the deck's real difficulties.
See `docs/PARAPHRASE_TEST.md`.

**Q: Why do the scores sometimes refuse to show a number?**
That's the give-up rule, on purpose. Memory needs ≥30 reviews, Performance ≥20 (with a mix of
right and wrong), Readiness ≥200 reviews **and** ≥50% coverage. Below the line it lists what's
missing rather than quoting a number the evidence can't support. Thresholds are constants at the
top of `gmat_scores.rs` and asserted by tests.

**Q: Are the AI-generated cards any good?**
There's a card-quality check (rubric 7f): a 50-item gold set of known-correct answers, cards
generated from one real source, and a checker that sorts every card into correct-useful /
wrong-fact / bad-teaching against a cutoff *fixed before the results*. Live run: **32 useful / 0
wrong-fact / 18 blocked** (all duplicates) — the gate correctly blocked real output. See
`docs/AI_CARD_CHECK.md`.

**Q: Does it work offline / can I turn the AI off?**
Yes. The engine grades, scores, selects, and breaks down topics with the network pulled — the
AI difficulty is a dev-time rating that degrades to the coarse `difficulty::` tag if absent.
Adaptive selection is toggle-gated and **off by default**; confidence-grading can be switched
off in iPhone settings or desktop Preferences. Crash test: killed mid-review 20×, 20/20 clean.
See `docs/CRASH_AND_OFFLINE.md`.

**Q: Is the Readiness number validated against real GMAT outcomes?**
Honestly, no — that needs students who studied *and* took real practice tests over time, which
isn't gatherable in a week. So we prove the **steps of the bridge** (calibrated memory →
performance on held-out questions → a documented 205–805 mapping with a range) and say plainly
the projected number isn't yet validated against practice-test outcomes. See `docs/scores/03-readiness.md`.
