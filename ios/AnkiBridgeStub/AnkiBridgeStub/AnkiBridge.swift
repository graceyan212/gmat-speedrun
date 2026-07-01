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

    var description: String {
        switch self {
        case .backendInit: return "rslib backend failed to initialize"
        case .openCollection(let rc): return "anki_open failed (rc=\(rc))"
        case .importPackage(let rc): return "anki_import_apkg failed (rc=\(rc))"
        case .selectDeck(let rc): return "anki_select_deck_by_name failed (rc=\(rc))"
        case .nextCard(let rc): return "anki_next_card failed (rc=\(rc))"
        case .answer(let rc): return "anki_answer_rating failed (rc=\(rc))"
        case .badResponse: return "could not decode rslib JSON response"
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

        // 2. Open (create) a collection in Application Support.
        let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask)[0]
        let dir = support.appendingPathComponent(subdir, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        // Start from a clean collection each launch so the import is deterministic.
        let colPath = dir.appendingPathComponent("collection.anki2").path
        try? FileManager.default.removeItem(atPath: colPath)
        let mediaDir = dir.appendingPathComponent("collection.media").path
        try? FileManager.default.createDirectory(atPath: mediaDir, withIntermediateDirectories: true)
        let mediaDB = dir.appendingPathComponent("collection.media.db2").path
        note("collection: \(colPath)")

        let orc = colPath.withCString { cp in
            mediaDir.withCString { mf in
                mediaDB.withCString { md in
                    anki_open(backendPtr, cp, mf, md)
                }
            }
        }
        note("anki_open rc=\(orc)")
        guard orc == 0 else { throw AnkiBridgeError.openCollection(orc) }

        // 3. Import the bundled GMAT .apkg through rslib.
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

    func close() {
        if backendPtr != 0 {
            _ = anki_close(backendPtr, false)
            anki_close_backend(backendPtr)
            backendPtr = 0
        }
    }

    deinit { close() }
}
