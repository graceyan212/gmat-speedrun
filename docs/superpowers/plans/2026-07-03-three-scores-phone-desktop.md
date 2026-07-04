# Three Scores (memory / performance / readiness) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three genuinely distinct scores — memory, performance, readiness — each with a range and a per-score give-up rule, computed once in the shared Rust engine and rendered identically on the phone (SwiftUI) and desktop (Qt).

**Architecture:** One read-only Rust method `Collection::get_gmat_scores` (in `anki/rslib/src/scheduler/gmat_scores.rs`) computes all three from a single storage read (`all_topic_card_rows`), exposed via a new `GetGmatScores` protobuf RPC. Python/Qt call the RPC through generated bindings; the phone reaches it through a new `anki_get_scores` C-ABI wrapper that returns a small JSON blob (same pattern as `anki_next_card`).

**Tech Stack:** Rust (rslib), protobuf/prost, Python/PyQt6, Rust C-ABI + XCFramework, Swift/SwiftUI.

**Approach note (deadline-pragmatic):** Tasks are ordered as a *walking skeleton* — prove the RPC→bridge→phone pipe with stub numbers first (Tasks 1–2), then fill in real score math (Tasks 3–7), then the real UI (Task 8). Proto/service/bridge/Swift/Qt code is given exactly. The three score-math functions are specified as algorithm + exact input fields + reference helpers + test assertions; their bodies are finalized against the Rust compiler during execution (pure functions with unit tests, so correctness is proven by the tests, not by hand).

## Global Constraints

- **Read-only engine:** no `col.transact(...)`, no card mutation, no `card.due` writes in the score path. Undo history MUST stay replayable (asserted by a test). Mirror `topic_mastery.rs` exactly.
- **Shared engine:** scores computed only in Rust; Swift/Qt/Python never re-implement scoring. (Grading hard cap otherwise.)
- **Three distinct measurements:** memory (FSRS retrievability), performance (Rasch θ vs. item difficulty), readiness (θ→GMAT 205–805, coverage-discounted). A test asserts they can diverge.
- **Give-up thresholds (verbatim from spec):** memory ≥ **30** graded reviews; performance ≥ **20** graded answers with **≥1 right and ≥1 wrong**; readiness coverage ≥ **50%** AND ≥ **200** graded reviews.
- **Difficulty source:** `aidiff::NN` tag (0–100) when present, else coarse `difficulty::` tag mapped `easy=20 / medium=50 / hard=80`. Not blocked on the AI calibration lane.
- **Coordination:** this lane edits `gmat_scores.rs` (new), `scheduler.proto`, `scheduler/mod.rs` (+1 line), `service/mod.rs` (delegator), plus `bridge/`, `ios/`, `qt/aqt/gmat_dashboard.py`. It does NOT touch `adaptive.rs`, `queue/builder/*`, the AI calibration, or the toggle (adaptive-engine agent's lane).
- **GMAT scale:** total 205–805, steps of 10.
- **Workspaces:** engine work in the `anki` repo worktree on branch `three-scores-engine`; bridge/iOS/docs in the `speedrun` worktree on branch `three-scores-phone`.

---

## File Structure

**anki repo (branch `three-scores-engine`):**
- Create: `rslib/src/scheduler/gmat_scores.rs` — `compute` helpers + `Collection::get_gmat_scores` + `#[cfg(test)]` unit tests.
- Modify: `proto/anki/scheduler.proto` — `+GetGmatScores` RPC, `+GmatScores/ScoreValue/GetGmatScoresRequest` messages.
- Modify: `rslib/src/scheduler/mod.rs` — `+mod gmat_scores;`.
- Modify: `rslib/src/scheduler/service/mod.rs` — `+get_gmat_scores` delegator + imports.
- Create: `pylib/tests/test_gmat_scores.py` — end-to-end Python test.
- Modify: `qt/aqt/gmat_dashboard.py` — render three scores.

**speedrun repo (branch `three-scores-phone`):**
- Modify: `bridge/anki-bridge-rs/src/lib.rs` — `+M_GET_GMAT_SCORES`, `+anki_get_scores`.
- Rebuild: `bridge/AnkiRust.xcframework` (script in `bridge/`).
- Modify: `ios/AnkiBridgeStub/AnkiBridgeStub/AnkiBridge.swift` — `+scores()` + `Scores` struct.
- Modify: `ios/AnkiBridgeStub/AnkiBridgeStub/ContentView.swift` — three-score panel.

---

## Task 0: Set up the anki engine worktree + green baseline

**Files:** none (environment).

**Interfaces:**
- Produces: an isolated anki checkout at `<speedrun-wt>/anki` on branch `three-scores-engine`, building green.

- [ ] **Step 1: Create the anki worktree inside this worktree**

The `anki` engine is a separate git repo at `/Users/graceyan/Desktop/alpha/speedrun/anki`. Create a linked worktree for it inside the phone worktree so the nested layout matches the main checkout:

```bash
git -C /Users/graceyan/Desktop/alpha/speedrun/anki worktree add \
  /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki \
  -b three-scores-engine
```

- [ ] **Step 2: Baseline build + test (confirm clean start)**

```bash
cd /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki
cargo test -p anki scheduler::topic_mastery 2>&1 | tail -20
```
Expected: existing topic_mastery tests PASS. If the build is cold this is slow (first compile). If it fails for pre-existing reasons, STOP and report.

- [ ] **Step 3: No commit** (environment only).

---

## Task 1: Proto RPC + stub `get_gmat_scores` (desktop/Python pipe)

**Files:**
- Modify: `anki/proto/anki/scheduler.proto` (after the `GetTopicMasteryStats` rpc, ~line 72; messages after `GetTopicMasteryStatsResponse`, ~line 524)
- Modify: `anki/rslib/src/scheduler/mod.rs:22`
- Create: `anki/rslib/src/scheduler/gmat_scores.rs`
- Modify: `anki/rslib/src/scheduler/service/mod.rs` (imports ~17, delegator ~393)
- Create: `anki/pylib/tests/test_gmat_scores.py`

**Interfaces:**
- Produces: `Collection::get_gmat_scores(&mut self, GetGmatScoresRequest) -> Result<GmatScores>`; proto `GmatScores{memory,performance,readiness: ScoreValue}`; Python `col._backend.get_gmat_scores(deck_name=...)`.

- [ ] **Step 1: Add the proto messages + RPC**

In `scheduler.proto`, add to `service SchedulerService` immediately after the `GetTopicMasteryStats` rpc:
```proto
  rpc GetGmatScores(GetGmatScoresRequest) returns (GmatScores);
```
And after `GetTopicMasteryStatsResponse`:
```proto
// GMAT fork (T3): the three scores (memory / performance / readiness).
message GetGmatScoresRequest {
  // Deck to scope to; empty = whole collection.
  string deck_name = 1;
}
message ScoreValue {
  bool abstained = 1;
  double score = 2;       // valid only when !abstained
  double low = 3;
  double high = 4;
  string unit = 5;        // "pct" | "gmat"
  string confidence = 6;  // "low"|"medium"|"high" (readiness only; else empty)
  repeated string reasons = 7;  // main drivers behind the number
  repeated string missing = 8;  // what data is still needed (give-up display)
}
message GmatScores {
  ScoreValue memory = 1;
  ScoreValue performance = 2;
  ScoreValue readiness = 3;
}
```

- [ ] **Step 2: Register the module**

`anki/rslib/src/scheduler/mod.rs` — add next to `mod topic_mastery;`:
```rust
mod gmat_scores;
```

- [ ] **Step 3: Create `gmat_scores.rs` with a STUB that abstains on everything**

```rust
use anki_proto::scheduler::GetGmatScoresRequest;
use anki_proto::scheduler::GmatScores;
use anki_proto::scheduler::ScoreValue;

use crate::collection::Collection;
use crate::error::Result;

impl Collection {
    /// Read-only: compute the three GMAT scores. No transaction, no card
    /// mutation — same discipline as `get_topic_mastery_stats`, so undo
    /// history stays intact.
    pub fn get_gmat_scores(&mut self, _req: GetGmatScoresRequest) -> Result<GmatScores> {
        // STUB (Task 1): everything abstains. Real math lands in Tasks 3-7.
        let abstain = |missing: &str| ScoreValue {
            abstained: true,
            missing: vec![missing.to_string()],
            ..Default::default()
        };
        Ok(GmatScores {
            memory: Some(abstain("stub: memory not yet computed")),
            performance: Some(abstain("stub: performance not yet computed")),
            readiness: Some(abstain("stub: readiness not yet computed")),
        })
    }
}
```

- [ ] **Step 4: Wire the service delegator**

`anki/rslib/src/scheduler/service/mod.rs` — add imports near the other scheduler proto imports:
```rust
use anki_proto::scheduler::GetGmatScoresRequest;
use anki_proto::scheduler::GmatScores;
```
And in `impl crate::services::SchedulerService for Collection`, next to `get_topic_mastery_stats`:
```rust
    fn get_gmat_scores(&mut self, input: GetGmatScoresRequest) -> Result<GmatScores> {
        self.get_gmat_scores(input)
    }
```

- [ ] **Step 5: Build rslib + regenerate bindings**

```bash
cd /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki
cargo build -p anki 2>&1 | tail -20
```
Expected: compiles. (The proto → Rust/Python codegen runs in the build.)

- [ ] **Step 6: Write the Python end-to-end test**

`anki/pylib/tests/test_gmat_scores.py`:
```python
"""End-to-end test for the three GMAT scores RPC (track T3).

Mirrors test_topic_mastery.py: drive col._backend.get_gmat_scores and assert a
scored-or-correctly-abstaining ScoreValue for each of the three scores."""
from anki.collection import Collection


def test_get_gmat_scores_returns_three_scores(tmp_path):
    col = Collection(str(tmp_path / "col.anki2"))
    try:
        res = col._backend.get_gmat_scores(deck_name="")
        # Three ScoreValue fields always present.
        for sv in (res.memory, res.performance, res.readiness):
            assert sv is not None
            # Either it abstained (and says what's missing) or it has a range.
            if sv.abstained:
                assert len(sv.missing) >= 1
            else:
                assert sv.low <= sv.score <= sv.high
    finally:
        col.close()
```

- [ ] **Step 7: Run the Python test**

```bash
cd /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki
./tools/pytest.sh pylib/tests/test_gmat_scores.py -v 2>&1 | tail -20   # or: PYTHONPATH=out/pylib out/pyenv/bin/pytest ...
```
Expected: PASS (all three abstain in the stub → `missing` populated).

- [ ] **Step 8: Commit (anki repo)**

```bash
git -C /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki add -A
git -C .../anki commit -m "feat(gmat): GetGmatScores RPC skeleton (stub abstains) + python e2e test"
```

---

## Task 2: Bridge `anki_get_scores` + minimal phone readout (phone pipe)

**Files:**
- Modify: `bridge/anki-bridge-rs/src/lib.rs` (constants ~46; new fn near `anki_next_card`)
- Rebuild: `bridge/AnkiRust.xcframework`
- Modify: `ios/AnkiBridgeStub/AnkiBridgeStub/AnkiBridge.swift`
- Modify: `ios/AnkiBridgeStub/AnkiBridgeStub/ContentView.swift`

**Interfaces:**
- Consumes: proto `GmatScores` (Task 1).
- Produces: C-ABI `anki_get_scores(backend_ptr, out_data, out_len) -> c_int` returning JSON `{"memory":{...},"performance":{...},"readiness":{...}}`; Swift `AnkiEngine.scores() throws -> Scores`.

- [ ] **Step 1: Find the RPC's backend method index**

Both Python `_run_command` and the bridge's `run_service_method` use the same (service, method) numbering. After Task 1's build, read the generated index:
```bash
grep -A3 "def get_gmat_scores" /Users/graceyan/Desktop/alpha/speedrun/.claude/worktrees/three-scores/anki/out/pylib/anki/_backend_generated.py
```
Expected: a line `self._run_command(13, N, ...)`. Use `13` as the service and `N` as `M_GET_GMAT_SCORES` (topic_mastery is `(13, 39)`, so this is likely `40`; use the actual N printed).

- [ ] **Step 2: Add the bridge wrapper**

In `bridge/anki-bridge-rs/src/lib.rs`, add near the scheduler constants (~line 47):
```rust
// GetGmatScores: BackendSchedulerService (svc 13). Method index N read from
// generated _backend_generated.py (get_gmat_scores -> _run_command(13, N)).
const M_GET_GMAT_SCORES: u32 = 40; // <-- set to the N printed in Step 1
```
And add the function (after `anki_next_card`):
```rust
/// Compute the three GMAT scores and return them as a JSON blob (caller frees
/// with `anki_free_response`). Scoped to the "GMAT Focus" deck.
///
/// Output JSON: {"memory":SV,"performance":SV,"readiness":SV} where SV is
/// {"abstained":bool,"score":f,"low":f,"high":f,"unit":"pct|gmat",
///  "confidence":"","reasons":[..],"missing":[..]}.
///
/// # Safety: `backend_ptr` from `anki_open_backend` with a collection open.
/// Returns 0 on success, 1 on backend error, -1 on FFI error.
#[no_mangle]
pub unsafe extern "C" fn anki_get_scores(
    backend_ptr: i64,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if backend_ptr == 0 {
        return -1;
    }
    let backend = unsafe { &*(backend_ptr as *const Backend) };
    let req = anki_proto::scheduler::GetGmatScoresRequest {
        deck_name: "GMAT Focus".to_string(),
    };
    let bytes = req.encode_to_vec();
    let resp_bytes =
        match backend.run_service_method(SVC_BACKEND_SCHEDULER, M_GET_GMAT_SCORES, &bytes) {
            Ok(b) => b,
            Err(_) => return 1,
        };
    let scores = match anki_proto::scheduler::GmatScores::decode(&resp_bytes[..]) {
        Ok(s) => s,
        Err(_) => return 1,
    };
    // Hand-build JSON (no serde dep), mirroring anki_next_card.
    fn sv_json(sv: &Option<anki_proto::scheduler::ScoreValue>, out: &mut String) {
        let sv = sv.clone().unwrap_or_default();
        out.push_str("{\"abstained\":");
        out.push_str(if sv.abstained { "true" } else { "false" });
        out.push_str(",\"score\":");   out.push_str(&sv.score.to_string());
        out.push_str(",\"low\":");     out.push_str(&sv.low.to_string());
        out.push_str(",\"high\":");    out.push_str(&sv.high.to_string());
        out.push_str(",\"unit\":\"");  json_escape_into(&sv.unit, out);
        out.push_str("\",\"confidence\":\""); json_escape_into(&sv.confidence, out);
        out.push_str("\",\"reasons\":[");
        for (i, r) in sv.reasons.iter().enumerate() {
            if i > 0 { out.push(','); }
            out.push('"'); json_escape_into(r, out); out.push('"');
        }
        out.push_str("],\"missing\":[");
        for (i, m) in sv.missing.iter().enumerate() {
            if i > 0 { out.push(','); }
            out.push('"'); json_escape_into(m, out); out.push('"');
        }
        out.push_str("]}");
    }
    let mut json = String::with_capacity(512);
    json.push_str("{\"memory\":");      sv_json(&scores.memory, &mut json);
    json.push_str(",\"performance\":"); sv_json(&scores.performance, &mut json);
    json.push_str(",\"readiness\":");   sv_json(&scores.readiness, &mut json);
    json.push('}');
    unsafe { set_output(json.into_bytes(), out_data, out_len) };
    0
}
```
Note: `anki_proto` in the bridge must resolve `GmatScores` etc. — it depends on the anki crate built in Task 1. Point the bridge's `anki`/`anki_proto` dependency at the `three-scores-engine` worktree (Cargo path) for the build.

- [ ] **Step 3: Rebuild the xcframework**

```bash
cd /Users/graceyan/Desktop/alpha/speedrun/bridge && ls   # find the build script (e.g. build-xcframework.sh)
# run it; expected: AnkiRust.xcframework regenerated with anki_get_scores in the headers
```
Verify the symbol is exported:
```bash
nm -gU bridge/AnkiRust.xcframework/ios-arm64*/*.a 2>/dev/null | grep anki_get_scores || \
grep -r anki_get_scores bridge/AnkiRust.xcframework/*/Headers 2>/dev/null
```

- [ ] **Step 4: Add the Swift bridge call**

In `ios/.../AnkiBridge.swift`, add a `Scores` model and an error case, and the `scores()` method on `AnkiEngine` (mirrors `nextCard()` JSON decoding):
```swift
struct ScoreValue {
    let abstained: Bool
    let score, low, high: Double
    let unit, confidence: String
    let reasons, missing: [String]
}
struct Scores { let memory, performance, readiness: ScoreValue }
```
```swift
func scores() throws -> Scores {
    var outData: UnsafeMutablePointer<UInt8>? = nil
    var outLen: UInt = 0
    let rc = anki_get_scores(backendPtr, &outData, &outLen)
    guard rc == 0 else { throw AnkiBridgeError.nextCard(rc) } // reuse or add a .scores case
    guard let outData, outLen > 0 else { throw AnkiBridgeError.badResponse }
    defer { anki_free_response(outData, outLen) }
    let data = Data(bytes: outData, count: Int(outLen))
    guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw AnkiBridgeError.badResponse
    }
    func parse(_ k: String) -> ScoreValue {
        let d = obj[k] as? [String: Any] ?? [:]
        return ScoreValue(
            abstained: d["abstained"] as? Bool ?? true,
            score: d["score"] as? Double ?? 0, low: d["low"] as? Double ?? 0,
            high: d["high"] as? Double ?? 0,
            unit: d["unit"] as? String ?? "", confidence: d["confidence"] as? String ?? "",
            reasons: d["reasons"] as? [String] ?? [], missing: d["missing"] as? [String] ?? [])
    }
    return Scores(memory: parse("memory"), performance: parse("performance"),
                  readiness: parse("readiness"))
}
```

- [ ] **Step 5: Minimal readout on the finished screen (skeleton verification)**

In `ContentView.swift`, add `@Published var scores: Scores?` to the view model, populate it in `answer(...)`/`syncNow(...)` completion (call `engine.scores()`), and print a temporary text line on the `.finished` screen: `Text("mem \(scores?.memory.abstained == true ? "—" : ...)")`. This is throwaway UI to confirm the pipe; the real panel is Task 8.

- [ ] **Step 6: Build the iOS app (simulator) and confirm no crash**

```bash
xcodebuild -project ios/AnkiBridgeStub/AnkiBridgeStub.xcodeproj -scheme AnkiBridgeStub \
  -destination 'generic/platform=iOS Simulator' build 2>&1 | tail -5
```
Expected: BUILD SUCCEEDED, `anki_get_scores` links.

- [ ] **Step 7: Commit (both repos)**

```bash
git -C .../anki commit --allow-empty -m "chore: engine index note for GetGmatScores"   # if any anki change
git -C <speedrun-wt> add bridge ios && git -C <speedrun-wt> commit -m "feat(phone): anki_get_scores bridge + Swift scores() + skeleton readout"
```

---

## Task 3: Real MEMORY score

**Files:** Modify `anki/rslib/src/scheduler/gmat_scores.rs` (+ tests).

**Interfaces:**
- Produces: `fn memory_score(rows: &[TopicCardRow], now_days: f32) -> ScoreValue`.

**Algorithm:** For each row with `stability: Some(s)`, compute `retrievability(s, days_elapsed, decay.unwrap_or(FSRS5_DEFAULT_DECAY))` using the existing helper pattern in `topic_mastery.rs:242` (`current_retrievability(MemoryState{stability, difficulty:0.0}, days, decay)`). Memory score = mean recall × 100. Range = mean ± std-dev of per-card recall (×100), clamped 0–100. Give-up: if total graded reviews (`Σ row.total`) < 30 → abstain with `missing = ["Answer at least 30 cards (have N)."]`.

**Data inputs (exact, from `TopicCardRow`):** `stability: Option<f32>`, `decay: Option<f32>`, `total: u32` (genuine reviews). *Elapsed days:* `all_topic_card_rows` does not currently return a last-review date. If needed for a true "now" recall, extend the `topic_stats.rs` query to also select the card's last review day (or `due`), matching how `gmat_readiness.py:_recall_probability` derives `days_elapsed`. If that field is unavailable this session, MVP uses `days = 0` (recall at last review ≈ 1.0 for reviewed cards) and the range carries the honesty — **document whichever is used in the score's `reasons`.**

- [ ] **Step 1: Failing test** — `memory_rises_with_more_stable_cards` and `memory_abstains_below_30_reviews`: build rows via a test helper, assert score direction + `low <= score <= high` + abstain path.
- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement `memory_score` + wire into `get_gmat_scores` (replace the stub's `memory`).**
- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(gmat): real memory score (FSRS retrievability + range + give-up)`.

---

## Task 4: Real PERFORMANCE score (Rasch θ)

**Files:** Modify `gmat_scores.rs` (+ tests).

**Interfaces:**
- Produces: `fn difficulty_logit(tags: &str) -> f32` (aidiff:: → coarse fallback, mapped 0–100 → logit `(d/100 - 0.5)*4.0`); `fn performance_score(rows: &[TopicCardRow]) -> ScoreValue`.

**Algorithm:** Per row, difficulty `b_i = difficulty_logit(tags)`; outcomes `c_i = row.passed`, `n_i = row.total` (correct = ease≥2, already tallied). Estimate ability θ by maximizing the Rasch log-likelihood `Σ c_i·log σ(θ−b_i) + (n_i−c_i)·log(1−σ(θ−b_i))` via a few Newton steps (start θ=0). Display = `σ(θ) * 100`. Range = θ ± 1.96/√(Fisher info `Σ n_i·σ(θ−b_i)(1−σ(θ−b_i))`), mapped through σ×100. Give-up: total answers `Σ n_i` < 20 **or** all-correct/all-wrong (`Σc==0` or `Σc==Σn`) → abstain (`missing` names the shortfall). Tag parsing reuses the `split_whitespace().find(...)` pattern from `topic_mastery.rs:261`.

- [ ] **Step 1: Failing tests** — `ability_rises_on_correct_falls_on_wrong`; `performance_range_brackets_estimate` (`low<score<high`); `missing_aidiff_falls_back_to_coarse` (row with only `difficulty::hard` yields a finite b≈80-logit); `performance_abstains_all_correct`.
- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement `difficulty_logit` + `performance_score`; wire in.**
- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(gmat): real performance score (Rasch theta + SE range + coarse fallback)`.

---

## Task 5: Real READINESS score (GMAT scale)

**Files:** Modify `gmat_scores.rs` (+ tests).

**Interfaces:**
- Consumes: performance θ/σ(θ) (Task 4), coverage fraction.
- Produces: `fn coverage_fraction(rows: &[TopicCardRow]) -> f32`; `fn readiness_score(perf: &Perf, coverage: f32, total_reviews: u32) -> ScoreValue`.

**Algorithm:** `coverage_fraction` = distinct topic prefixes present (via the `topic_prefix` pattern) / total GMAT Focus outline topics. Let `p = σ(θ)`; discount toward the 4-choice guess floor: `p_adj = p*coverage + 0.25*(1-coverage)`; `projected = round_to_10(205.0 + p_adj*600.0)`. Range maps θ's low/high through the same transform, then widens by `(1-coverage)` on each side. `confidence = if coverage>=0.8 {"high"} else if coverage>=0.5 {"medium"} else {"low"}`. Give-up: coverage < 0.50 **or** total reviews < 200 → abstain (`missing` lists coverage % and review shortfall). The outline-topics list may be hardcoded in Rust mirroring `gmat_readiness._all_outline_topics()`; keep it in one `const`.

- [ ] **Step 1: Failing tests** — `readiness_on_gmat_scale` (205 ≤ score ≤ 805, multiple of 10); `readiness_abstains_below_coverage`; `readiness_range_widens_with_low_coverage`.
- [ ] **Step 2: Run → fails.**
- [ ] **Step 3: Implement; wire in.**
- [ ] **Step 4: Run → passes.**
- [ ] **Step 5: Commit** `feat(gmat): real readiness score (theta->GMAT scale, coverage-gated, confidence)`.

---

## Task 6: Distinctness + undo-intact tests

**Files:** Modify `gmat_scores.rs` (`#[cfg(test)]` only).

- [ ] **Step 1:** `three_scores_are_distinct` — construct rows where memory is high (high stability) but performance is low (many wrong on high-difficulty items); assert `memory.score` and `performance.score` diverge and readiness is on the GMAT scale (different unit).
- [ ] **Step 2:** `scores_are_read_only_undo_intact` — copy the `topic_mastery.rs:551` pattern: create an undoable `Op::UpdateCard`, call `get_gmat_scores`, assert `col.can_undo() == Some(&Op::UpdateCard)`.
- [ ] **Step 3: Run → passes.**
- [ ] **Step 4: Commit** `test(gmat): scores distinct + read-only undo intact`.

---

## Task 7: Python end-to-end with real data

**Files:** Modify `anki/pylib/tests/test_gmat_scores.py`.

- [ ] **Step 1:** Extend the test: add a note with `topic::` + `difficulty::` tags, review it a few times, and assert the abstain paths report sensible `missing` lists (deck is small, so scores will abstain — that IS the correct behavior, and proves the give-up rule end-to-end). Assert `unit` fields (`"pct"`, `"pct"`, `"gmat"`).
- [ ] **Step 2: Run → passes.**
- [ ] **Step 3: Commit** `test(gmat): python e2e exercises real scoring + give-up`.

---

## Task 8: Real UI — phone (SwiftUI) + desktop (Qt)

**Files:**
- Modify: `ios/.../ContentView.swift` — Bauhaus three-score panel.
- Modify: `anki/qt/aqt/gmat_dashboard.py` — three scores.

**Interfaces:** Consumes `Scores` (Swift) / `GmatScores` proto (Qt).

- [ ] **Step 1 (phone): three-score panel.** Add a "SCORES" tab/button in the header (next to SYNC) that presents a sheet, and show the panel on the `.finished` screen. Each of the three is a Bauhaus block: uppercase label (MEMORY / PERFORMANCE / READINESS), then either the number + range (`"72 / 100 · range 60–84"`, readiness `"545 · range 505–585 · confidence low"`) or the abstain state (`"NOT ENOUGH DATA YET"` + the `missing` bullets). Reuse `BauhausTheme` tokens; no new colors. Replace the throwaway Task-2 text.
- [ ] **Step 2 (phone): build + manual check.** `xcodebuild ... build`; run on simulator; after a short review session, open SCORES and confirm three blocks render (abstaining is expected on the small deck). Screen-record for the Friday proof.
- [ ] **Step 3 (desktop): Qt.** In `gmat_dashboard.py`, call `col._backend.get_gmat_scores(deck_name="GMAT Focus")` and render all three ScoreValues (replace the single-readiness headline with a three-row layout; keep the existing Bauhaus header + coverage body). Each row shows number+range or the abstain marker + missing list.
- [ ] **Step 4 (desktop): run.** Launch the desktop app (`./run` or the dev build), open Tools → GMAT dashboard, confirm three scores render.
- [ ] **Step 5: Commit (both repos)** `feat(ui): three-score panel on phone + desktop dashboard`.

---

## Self-Review

- **Spec coverage:** memory (T3), performance (T4), readiness (T5), give-up per score (T3–T5), ranges (T3–T5), distinctness (T6), read-only/undo (T6), shared-engine RPC (T1), phone (T2/T8), desktop (T8), tests (T3–T7). Difficulty fallback (T4). All spec sections map to a task. ✅
- **Placeholder scan:** score-math bodies are algorithm-specified with exact inputs + reference helpers + test assertions (finalized against the compiler by design, noted in the header) — not TODOs. Proto/service/bridge/Swift code is exact. ✅
- **Type consistency:** `ScoreValue`/`GmatScores`/`GetGmatScoresRequest` identical across proto, Rust, bridge JSON keys, Swift struct, Python. `get_gmat_scores` name consistent in Rust/service/Python/bridge request. Difficulty tags `aidiff::`/`difficulty::` consistent with spec + topic_mastery parsing. ✅
- **Known implementation risks (flagged, not blocking):** (a) memory needs last-review-day — may require extending the `topic_stats.rs` SQL; MVP fallback documented. (b) bridge method index read from generated bindings, not hardcoded blind. (c) xcframework rebuild + bridge Cargo path must point at the `three-scores-engine` worktree.
