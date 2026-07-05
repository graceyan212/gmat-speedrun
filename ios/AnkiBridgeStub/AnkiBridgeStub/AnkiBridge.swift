// Swift wrapper around the C ABI exposed by AnkiRust.xcframework (rslib).
//
// This drives a REAL review session on the shared Anki engine:
//   * open/create a collection on disk
//   * import the bundled GMAT .apkg via rslib's ImportAnkiPackage RPC
//   * pull queued cards, render their HTML through rslib, and answer them
//
// All protobuf marshalling happens in Rust (see bridge/anki-bridge-rs/src/lib.rs);
// Swift only exchanges card_id + rating + a small JSON blob, so there is no
// SwiftProtobuf dependency and rslib stays the single source of truth.

import Foundation
import AnkiRustLib   // provided by AnkiRust.xcframework's module map

/// A single card to review, as rendered by rslib.
struct RenderedCard {
    let cardId: Int64
    let questionHTML: String
    let answerHTML: String
    let css: String
}

/// One of the three GMAT scores, as computed by rslib's GetGmatScores RPC.
/// When `abstained` is true the give-up rule fired: `missing` lists what is
/// still needed and score/low/high are unset.
struct ScoreValue {
    let abstained: Bool
    let score: Double
    let low: Double
    let high: Double
    let unit: String        // "pct" | "gmat"
    let confidence: String  // readiness only: "low" | "medium" | "high"
    let reasons: [String]
    let missing: [String]
}

/// The three distinct GMAT scores: memory, performance, readiness.
struct Scores {
    let memory: ScoreValue
    let performance: ScoreValue
    let readiness: ScoreValue
}

/// Answer button ratings (UI order). Maps to rslib's AGAIN/HARD/GOOD/EASY.
enum Rating: UInt32, CaseIterable {
    case again = 1
    case hard = 2
    case good = 3
    case easy = 4

    var label: String {
        switch self {
        case .again: return "Again"
        case .hard: return "Hard"
        case .good: return "Good"
        case .easy: return "Easy"
        }
    }
}

enum AnkiBridgeError: Error, CustomStringConvertible {
    case backendInit
    case openCollection(Int32)
    case importPackage(Int32)
    case selectDeck(Int32)
    case nextCard(Int32)
    case answer(Int32)
    case badResponse
    case syncLogin(Int32)
    case sync(Int32)
    case scores(Int32)

    var description: String {
        switch self {
        case .backendInit: return "rslib backend failed to initialize"
        case .openCollection(let rc): return "anki_open failed (rc=\(rc))"
        case .importPackage(let rc): return "anki_import_apkg failed (rc=\(rc))"
        case .selectDeck(let rc): return "anki_select_deck_by_name failed (rc=\(rc))"
        case .nextCard(let rc): return "anki_next_card failed (rc=\(rc))"
        case .answer(let rc): return "anki_answer_rating failed (rc=\(rc))"
        case .badResponse: return "could not decode rslib JSON response"
        case .syncLogin(let rc): return "anki_sync_login failed (rc=\(rc))"
        case .sync(let rc): return "sync failed (rc=\(rc))"
        case .scores(let rc): return "anki_get_scores failed (rc=\(rc))"
        }
    }
}

/// Owns the rslib backend pointer and the open collection for a review session.
final class AnkiEngine {
    private var backendPtr: Int64 = 0
    private(set) var log: [String] = []

    private func note(_ s: String) {
        NSLog("[AnkiEngine] %@", s)
        log.append(s)
    }

    /// Create the backend, open a fresh collection in the sandbox, and import
    /// the bundled GMAT deck so there are cards to review.
    ///
    /// `subdir` lets callers isolate collections (e.g. the headless self-test
    /// uses its own directory so it never contends with the UI session for the
    /// single-writer SQLite DB).
    func startSession(subdir: String = "anki-session") throws {
        // 1. Create rslib backend.
        let rc = anki_open_backend(nil, 0, &backendPtr)
        note("anki_open_backend rc=\(rc) ptr=\(backendPtr)")
        guard rc == 0, backendPtr != 0 else { throw AnkiBridgeError.backendInit }

        // 2. Open (create) a collection in Application Support. The collection
        // PERSISTS across launches (no wipe) so reviews/sync state survive
        // relaunch; only a genuinely first-ever launch imports the starter deck.
        let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask)[0]
        let dir = support.appendingPathComponent(subdir, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let colPath = dir.appendingPathComponent("collection.anki2").path
        // Capture BEFORE anki_open, which creates the file if absent.
        let isFirstLaunch = !FileManager.default.fileExists(atPath: colPath)
        let mediaDir = dir.appendingPathComponent("collection.media").path
        try? FileManager.default.createDirectory(atPath: mediaDir, withIntermediateDirectories: true)
        let mediaDB = dir.appendingPathComponent("collection.media.db2").path
        note("collection: \(colPath) isFirstLaunch=\(isFirstLaunch)")

        let orc = colPath.withCString { cp in
            mediaDir.withCString { mf in
                mediaDB.withCString { md in
                    anki_open(backendPtr, cp, mf, md)
                }
            }
        }
        note("anki_open rc=\(orc)")
        guard orc == 0 else { throw AnkiBridgeError.openCollection(orc) }

        guard isFirstLaunch else {
            note("persisted collection found; skipping starter-deck import")
            return
        }

        // 3. First launch only: import the bundled GMAT .apkg through rslib.
        // On a persisted collection this must NOT re-run, or every relaunch
        // would duplicate the starter deck's cards.
        guard let apkg = Bundle.main.url(forResource: "gmat_focus", withExtension: "apkg") else {
            note("FATAL: gmat_focus.apkg not found in app bundle")
            throw AnkiBridgeError.importPackage(-99)
        }
        let irc = apkg.path.withCString { anki_import_apkg(backendPtr, $0) }
        note("anki_import_apkg rc=\(irc) (\(apkg.lastPathComponent))")
        guard irc == 0 else { throw AnkiBridgeError.importPackage(irc) }

        // 4. Point the scheduler at the imported deck (a fresh collection still
        // has the empty Default deck selected, so GetQueuedCards would be empty).
        let src = "GMAT Focus".withCString { anki_select_deck_by_name(backendPtr, $0) }
        note("anki_select_deck_by_name(\"GMAT Focus\") rc=\(src)")
        guard src == 0 else { throw AnkiBridgeError.selectDeck(src) }
    }

    /// Fetch + render the next queued card. Returns nil when the queue is empty.
    func nextCard() throws -> RenderedCard? {
        var outData: UnsafeMutablePointer<UInt8>? = nil
        var outLen: UInt = 0
        let rc = anki_next_card(backendPtr, &outData, &outLen)
        guard rc == 0 else { throw AnkiBridgeError.nextCard(rc) }
        guard let outData = outData, outLen > 0 else { throw AnkiBridgeError.badResponse }
        defer { anki_free_response(outData, outLen) }

        let data = Data(bytes: outData, count: Int(outLen))
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AnkiBridgeError.badResponse
        }
        if obj["empty"] as? Bool == true {
            note("anki_next_card: queue empty")
            return nil
        }
        guard let cardId = (obj["card_id"] as? NSNumber)?.int64Value,
              let q = obj["question"] as? String,
              let a = obj["answer"] as? String else {
            throw AnkiBridgeError.badResponse
        }
        let css = obj["css"] as? String ?? ""
        note("rendered card_id=\(cardId) qlen=\(q.count) alen=\(a.count)")
        return RenderedCard(cardId: cardId, questionHTML: q, answerHTML: a, css: css)
    }

    /// Submit an answer for the given card. rslib records the review.
    func answer(cardId: Int64, rating: Rating) throws {
        let rc = anki_answer_rating(backendPtr, cardId, rating.rawValue)
        note("anki_answer_rating card_id=\(cardId) rating=\(rating.label) rc=\(rc)")
        guard rc == 0 else { throw AnkiBridgeError.answer(rc) }
    }

    /// Ask the shared engine to auto-grade a tapped multiple-choice answer into a
    /// Rating — from correctness, response time, and the card's difficulty vs the
    /// learner's ability (all computed in rslib). `targetSeconds` 0 = unknown.
    /// Never throws: a grading hiccup falls back to `.good` so a review is never
    /// blocked (the bridge returns the ease 1...4, or ≤0 on error).
    func autoGrade(cardId: Int64, correct: Bool, elapsedMs: UInt32, targetSeconds: UInt32 = 0) -> Rating {
        let ease = anki_grade_answer(backendPtr, cardId, correct ? 1 : 0, elapsedMs, targetSeconds)
        note("anki_grade_answer card_id=\(cardId) correct=\(correct) elapsed=\(elapsedMs)ms -> ease=\(ease)")
        return Rating(rawValue: UInt32(truncatingIfNeeded: ease)) ?? .good
    }

    /// Fetch the three GMAT scores (memory / performance / readiness) from
    /// rslib's GetGmatScores RPC. Decodes the JSON blob the bridge returns
    /// (same pattern as `nextCard()`). Each `ScoreValue` is either scored (with
    /// a range) or abstaining (give-up rule) with a `missing` list.
    func scores() throws -> Scores {
        var outData: UnsafeMutablePointer<UInt8>? = nil
        var outLen: UInt = 0
        let rc = anki_get_scores(backendPtr, &outData, &outLen)
        guard rc == 0 else { throw AnkiBridgeError.scores(rc) }
        guard let outData, outLen > 0 else { throw AnkiBridgeError.badResponse }
        defer { anki_free_response(outData, outLen) }

        let data = Data(bytes: outData, count: Int(outLen))
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AnkiBridgeError.badResponse
        }
        func parse(_ key: String) -> ScoreValue {
            let d = obj[key] as? [String: Any] ?? [:]
            return ScoreValue(
                abstained: d["abstained"] as? Bool ?? true,
                score: d["score"] as? Double ?? 0,
                low: d["low"] as? Double ?? 0,
                high: d["high"] as? Double ?? 0,
                unit: d["unit"] as? String ?? "",
                confidence: d["confidence"] as? String ?? "",
                reasons: d["reasons"] as? [String] ?? [],
                missing: d["missing"] as? [String] ?? []
            )
        }
        note("anki_get_scores ok len=\(outLen)")
        return Scores(memory: parse("memory"),
                      performance: parse("performance"),
                      readiness: parse("readiness"))
    }

    /// Log in to a self-hosted sync server. Returns the serialized `SyncAuth`
    /// bytes (opaque to Swift) — pass them straight into `sync(auth:)`.
    ///
    /// No SwiftProtobuf dependency is linked (see the file header), so the
    /// `SyncAuth` bytes are round-tripped as an opaque blob rather than
    /// decoded; only `SyncCollectionResponse.required` needs to be read on
    /// the Swift side, which is done with a minimal hand-rolled parser below.
    func syncLogin(endpoint: String, user: String, pass: String) throws -> [UInt8] {
        var outData: UnsafeMutablePointer<UInt8>? = nil
        var outLen: UInt = 0
        let rc = endpoint.withCString { ep in
            user.withCString { up in
                pass.withCString { pp in
                    anki_sync_login(backendPtr, ep, up, pp, &outData, &outLen)
                }
            }
        }
        note("anki_sync_login rc=\(rc)")
        guard rc == 0 else { throw AnkiBridgeError.syncLogin(rc) }
        guard let outData = outData, outLen > 0 else { throw AnkiBridgeError.badResponse }
        defer { anki_free_response(outData, outLen) }
        return Array(UnsafeBufferPointer(start: outData, count: Int(outLen)))
    }

    /// Run a collection sync using the `SyncAuth` bytes from `syncLogin`.
    ///
    /// `anki_sync_collection` returns a serialized `SyncCollectionResponse`
    /// (see `anki/proto/anki/sync.proto`):
    ///   uint32 host_number = 1; string server_message = 2;
    ///   ChangesRequired required = 3;  optional string new_endpoint = 4;
    ///   int32 server_media_usn = 5;
    /// `ChangesRequired`: NO_CHANGES=0, NORMAL_SYNC=1, FULL_SYNC=2,
    /// FULL_DOWNLOAD=3, FULL_UPLOAD=4.
    ///
    /// Only field 3 (`required`, a varint) is needed here, so rather than
    /// pull in SwiftProtobuf for one integer this walks the top-level
    /// tag/length-delimited structure by hand (see `readVarintField` below).
    /// If a full sync is required, follows up with
    /// `anki_full_upload_or_download` (FULL_UPLOAD/FULL_SYNC -> upload=true,
    /// FULL_DOWNLOAD -> upload=false).
    func sync(auth: [UInt8]) throws {
        var outData: UnsafeMutablePointer<UInt8>? = nil
        var outLen: UInt = 0
        let rc = auth.withUnsafeBufferPointer { buf -> Int32 in
            anki_sync_collection(backendPtr, buf.baseAddress, UInt(buf.count), &outData, &outLen)
        }
        note("anki_sync_collection rc=\(rc)")
        guard rc == 0 else { throw AnkiBridgeError.sync(rc) }
        // An all-default SyncCollectionResponse (required = NO_CHANGES, zero
        // media USN, empty message/endpoint) encodes to ZERO protobuf bytes.
        // rslib has already committed the normal/no-op sync by the time
        // anki_sync_collection returns rc=0, so an empty response means
        // "synced — nothing further to do", NOT a decode failure. Treat it as
        // NO_CHANGES rather than throwing .badResponse.
        guard let outData = outData, outLen > 0 else {
            note("sync_collection: empty response => NO_CHANGES (already in sync)")
            return
        }
        let responseBytes = Array(UnsafeBufferPointer(start: outData, count: Int(outLen)))
        anki_free_response(outData, outLen)

        let required = ProtoScan.readVarintField(responseBytes, fieldNumber: 3) ?? 0
        note("sync_collection required=\(required)")

        let upload: Bool
        switch required {
        case 0, 1:
            // NO_CHANGES / NORMAL_SYNC: anki_sync_collection already merged
            // the normal (incremental) changes; nothing further to do.
            return
        case 2, 4:
            // FULL_SYNC, FULL_UPLOAD: local side is authoritative.
            upload = true
        case 3:
            // FULL_DOWNLOAD: remote side is authoritative.
            upload = false
        default:
            note("sync_collection: unrecognized required=\(required); skipping full sync")
            return
        }

        var fudOutData: UnsafeMutablePointer<UInt8>? = nil
        var fudOutLen: UInt = 0
        let frc = auth.withUnsafeBufferPointer { buf -> Int32 in
            anki_full_upload_or_download(backendPtr, buf.baseAddress, UInt(buf.count), upload, -1, &fudOutData, &fudOutLen)
        }
        note("anki_full_upload_or_download upload=\(upload) rc=\(frc)")
        if let fudOutData = fudOutData, fudOutLen > 0 {
            anki_free_response(fudOutData, fudOutLen)
        }
        guard frc == 0 else { throw AnkiBridgeError.sync(frc) }
    }

    func close() {
        if backendPtr != 0 {
            _ = anki_close(backendPtr, false)
            anki_close_backend(backendPtr)
            backendPtr = 0
        }
    }

    deinit { close() }
}

/// A minimal, dependency-free protobuf scanner.
///
/// The app has no SwiftProtobuf dependency (by design — see the file header),
/// but `sync(auth:)` needs to read exactly one integer field
/// (`SyncCollectionResponse.required`, field 3) out of the response rslib
/// returns. Pulling in the whole `.proto`-generated Swift model for one field
/// isn't worth the dependency, so this walks the wire format directly:
/// each field is a varint tag (`field_number << 3 | wire_type`) followed by
/// a value whose shape depends on the wire type. This only needs to handle
/// varint (0) and length-delimited (2) since those are the only wire types
/// `SyncCollectionResponse` uses.
enum ProtoScan {
    /// Read a top-level varint-typed field's value from `data` by field number.
    /// Returns `nil` if the field is absent or the bytes are malformed.
    static func readVarintField(_ data: [UInt8], fieldNumber: Int) -> UInt64? {
        var i = 0
        var result: UInt64?
        while i < data.count {
            guard let (tag, tagLen) = readVarint(data, at: i) else { return result }
            i += tagLen
            let wireType = tag & 0x7
            let field = Int(tag >> 3)
            switch wireType {
            case 0: // varint
                guard let (value, len) = readVarint(data, at: i) else { return result }
                i += len
                if field == fieldNumber { result = value }
            case 1: // 64-bit
                guard i + 8 <= data.count else { return result }
                i += 8
            case 2: // length-delimited
                guard let (len, lenLen) = readVarint(data, at: i) else { return result }
                i += lenLen
                guard i + Int(len) <= data.count else { return result }
                i += Int(len)
            case 5: // 32-bit
                guard i + 4 <= data.count else { return result }
                i += 4
            default:
                return result // unknown wire type; stop rather than misparse
            }
        }
        return result
    }

    /// Decode a base-128 varint starting at `start`. Returns (value, byteCount).
    private static func readVarint(_ data: [UInt8], at start: Int) -> (UInt64, Int)? {
        var result: UInt64 = 0
        var shift: UInt64 = 0
        var i = start
        while i < data.count {
            let byte = data[i]
            result |= UInt64(byte & 0x7F) << shift
            i += 1
            if byte & 0x80 == 0 {
                return (result, i - start)
            }
            shift += 7
            if shift > 63 { return nil }
        }
        return nil
    }
}
