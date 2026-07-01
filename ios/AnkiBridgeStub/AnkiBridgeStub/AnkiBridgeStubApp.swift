// SwiftUI app that links AnkiRust.xcframework and runs a real review session
// (load GMAT deck -> render cards -> answer) on rslib.

import SwiftUI

@main
struct AnkiBridgeStubApp: App {
    init() {
        // Headless self-test: when launched with ANKI_SELFTEST=1, drive the
        // exact same engine calls the UI buttons make (import -> select deck ->
        // next/answer loop), logging each step. This proves the review loop
        // end-to-end without needing a touch-injection harness on the simulator.
        if ProcessInfo.processInfo.environment["ANKI_SELFTEST"] == "1" {
            ReviewSelfTest.run(cardCount: 12)
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

/// Exercises the review loop programmatically (same FFI path as the buttons).
enum ReviewSelfTest {
    static func run(cardCount: Int) {
        let engine = AnkiEngine()
        do {
            try engine.startSession(subdir: "anki-selftest")
            var answered = 0
            // Alternate ratings so we exercise all four SchedulingStates paths.
            let ratings: [Rating] = [.again, .hard, .good, .easy]
            while answered < cardCount {
                guard let card = try engine.nextCard() else {
                    NSLog("[SelfTest] queue empty after %d answers", answered)
                    break
                }
                let qPreview = String(card.questionHTML.prefix(60))
                    .replacingOccurrences(of: "\n", with: " ")
                NSLog("[SelfTest] card #%d id=%lld q=\"%@…\"",
                      answered + 1, card.cardId, qPreview)
                let rating = ratings[answered % ratings.count]
                try engine.answer(cardId: card.cardId, rating: rating)
                answered += 1
                NSLog("[SelfTest] answered #%d with %@", answered, rating.label)
            }
            NSLog("[SelfTest] DONE: answered %d cards on rslib", answered)
        } catch {
            NSLog("[SelfTest] FAILED: %@", "\(error)")
        }
    }
}
