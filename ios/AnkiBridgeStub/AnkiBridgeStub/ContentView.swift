import SwiftUI

/// Drives a real review session on rslib: loads the GMAT deck, renders each card
/// through the engine, and records Again/Hard/Good/Easy answers.
@MainActor
final class ReviewViewModel: ObservableObject {
    enum Phase {
        case loading
        case reviewing
        case finished
        case error(String)
    }

    @Published var phase: Phase = .loading
    @Published var card: RenderedCard? {
        didSet {
            // New card on screen: restart the pace timer and clear the prior
            // answer / confidence / grade state.
            cardShownAt = Date()
            autoRating = nil
            pickedLetter = nil
            awaitingConfidence = false
            lastOverconfident = false
        }
    }
    @Published var showingAnswer = false
    @Published var answeredCount = 0
    /// The three GMAT scores, refreshed after each card / sync. nil until first load.
    @Published var scores: Scores?

    // --- Confidence-based grading + calibration / pacing ---
    // The engine decides Again/Hard/Good/Easy from correctness × your confidence;
    // time is shown for pacing feedback, never used to grade.
    /// On by default; persisted. When off, the manual Show Answer + 4-button flow.
    @Published var autoGradeEnabled = (UserDefaults.standard.object(forKey: "autoGradeEnabled") as? Bool) ?? true {
        didSet { UserDefaults.standard.set(autoGradeEnabled, forKey: "autoGradeEnabled") }
    }
    /// When the current question was shown (for the passive pace readout).
    private var cardShownAt = Date()
    /// The choice letter tapped (set on tap; the confidence prompt follows).
    @Published var pickedLetter: String?
    /// True after a choice is tapped, while we ask for confidence (before reveal).
    @Published var awaitingConfidence = false
    /// The rating the engine assigned once confidence was given.
    @Published var autoRating: Rating?
    /// The just-graded answer was an overconfident miss (wrong + not guessing).
    @Published var lastOverconfident = false
    /// Seconds the last answer took — shown as pacing feedback, not used to grade.
    @Published var lastElapsed: TimeInterval = 0

    // Session calibration + pacing tallies (shown on the finished screen).
    @Published var confidentCount = 0
    @Published var confidentCorrect = 0
    @Published var overconfidentMisses = 0
    @Published var skips = 0
    @Published var slowCount = 0

    // Sync settings + status. `serverURL` must carry a trailing slash
    // (rslib's sync client does `Url::join("sync/")`). serverURL/user/pass
    // persist to UserDefaults so they survive app relaunches — paste once.
    // (Keychain would be the production choice for the password; UserDefaults
    // is fine for this self-hosted demo.)
    @Published var serverURL = UserDefaults.standard.string(forKey: "syncServerURL") ?? "" {
        didSet { UserDefaults.standard.set(serverURL, forKey: "syncServerURL") }
    }
    @Published var syncUser = UserDefaults.standard.string(forKey: "syncUser") ?? "" {
        didSet { UserDefaults.standard.set(syncUser, forKey: "syncUser") }
    }
    @Published var syncPass = UserDefaults.standard.string(forKey: "syncPass") ?? "" {
        didSet { UserDefaults.standard.set(syncPass, forKey: "syncPass") }
    }
    @Published var syncStatus = ""
    @Published var isSyncing = false

    /// True when server URL + username + password are all set — enough to sync
    /// directly without opening the settings sheet.
    var hasCredentials: Bool {
        !serverURL.isEmpty && !syncUser.isEmpty && !syncPass.isEmpty
    }

    /// Fire a background sync only if configured — used on launch and after each
    /// answer so reviews propagate without a manual tap. No-op when unconfigured;
    /// syncNow() itself skips if a sync is already running.
    func autoSync() {
        guard hasCredentials else { return }
        syncNow()
    }

    private let engine = AnkiEngine()

    func start() {
        // rslib work is synchronous + CPU-bound; run off the main thread, then
        // publish results back on main.
        Task.detached(priority: .userInitiated) { [engine] in
            do {
                try engine.startSession()
                let first = try engine.nextCard()
                let sc = try? engine.scores()
                await MainActor.run {
                    self.scores = sc
                    if let first {
                        self.card = first
                        self.phase = .reviewing
                    } else {
                        self.phase = .finished
                    }
                    self.autoSync()  // pull remote changes on launch (if configured)
                }
            } catch {
                await MainActor.run { self.phase = .error("\(error)") }
            }
        }
    }

    func answer(_ rating: Rating) {
        guard let current = card else { return }
        Task.detached(priority: .userInitiated) { [engine] in
            do {
                try engine.answer(cardId: current.cardId, rating: rating)
                let next = try engine.nextCard()
                let sc = try? engine.scores()
                await MainActor.run {
                    self.scores = sc
                    self.answeredCount += 1
                    self.showingAnswer = false
                    if let next {
                        self.card = next
                    } else {
                        self.card = nil
                        self.phase = .finished
                    }
                    self.autoSync()  // push this review up after every answer (if configured)
                }
            } catch {
                await MainActor.run { self.phase = .error("\(error)") }
            }
        }
    }

    /// A choice was tapped: remember it + the response time, then ask for
    /// confidence (we grade once the student commits a confidence level).
    func handleChoiceTap(_ letter: String) {
        guard card != nil, !showingAnswer, !awaitingConfidence else { return }
        lastElapsed = Date().timeIntervalSince(cardShownAt)
        pickedLetter = letter.uppercased()
        awaitingConfidence = true
    }

    /// The student committed a confidence for their pick: grade correctness ×
    /// confidence via the shared engine, reveal the answer, and update the
    /// session's calibration + pacing tallies. The rating is applied on Next
    /// (or overridden by a manual button).
    func submitConfidence(_ confidence: Confidence) {
        guard let current = card, let picked = pickedLetter, awaitingConfidence else { return }
        let correct = picked == correctLetter(current)
        autoRating = engine.autoGrade(correct: correct, confidence: confidence)
        lastOverconfident = !correct && confidence != .guessing
        if confidence != .guessing {
            confidentCount += 1
            if correct { confidentCorrect += 1 }
        }
        if lastOverconfident { overconfidentMisses += 1 }
        if lastElapsed > 120 { slowCount += 1 }
        awaitingConfidence = false
        showingAnswer = true
    }

    /// Flag & skip — the pacing "cut your losses" decision. Records the card as
    /// Again and moves on.
    func flagSkip() {
        guard card != nil, !showingAnswer else { return }
        skips += 1
        answer(.again)
    }

    /// The correct choice letter for a card, parsed from its answer HTML (the
    /// deck marks it "Answer: X"). Nil for a non-multiple-choice card.
    private func correctLetter(_ card: RenderedCard) -> String? {
        guard let r = card.answerHTML.range(
            of: "Answer:\\s*(?:</b>)?\\s*([A-E])",
            options: [.regularExpression, .caseInsensitive]
        ) else { return nil }
        return String(card.answerHTML[r]).last.map { String($0).uppercased() }
    }

    /// True only for single-answer A–E multiple-choice cards (has an A) and a B)
    /// choice). Free-response, two-part, or passage cards without lettered
    /// options return false → the reviewer uses the manual Show Answer + rating
    /// flow for them instead of the tap-a-choice / confidence flow.
    func cardIsMultipleChoice(_ card: RenderedCard) -> Bool {
        func hasChoice(_ letter: String) -> Bool {
            card.questionHTML.range(
                of: "(^|>|\\n)\\s*\(letter)[).]\\s",
                options: [.regularExpression]
            ) != nil
        }
        return hasChoice("A") && hasChoice("B")
    }

    /// Log in to the configured sync server and run a collection sync.
    /// Sync is blocking network I/O, so — same pattern as `start()`/`answer()`
    /// — this runs off the main thread and only publishes back on main.
    func syncNow() {
        guard !isSyncing else { return }
        let endpoint = serverURL
        let user = syncUser
        let pass = syncPass
        isSyncing = true
        syncStatus = "Syncing…"
        Task.detached(priority: .userInitiated) { [engine] in
            do {
                let auth = try engine.syncLogin(endpoint: endpoint, user: user, pass: pass)
                try engine.sync(auth: auth)
                // A sync can change the card currently on screen (its stashed
                // scheduling state goes stale, which makes the next answer fail),
                // so reload the queue afterward to pick up a fresh card + state.
                let refreshed = try? engine.nextCard()
                let sc = try? engine.scores()
                await MainActor.run {
                    self.scores = sc
                    self.isSyncing = false
                    self.syncStatus = "Synced"
                    self.showingAnswer = false
                    if let refreshed {
                        self.card = refreshed
                        self.phase = .reviewing
                    } else {
                        self.card = nil
                        self.phase = .finished
                    }
                }
            } catch {
                await MainActor.run {
                    self.isSyncing = false
                    self.syncStatus = "Sync failed: \(error)"
                }
            }
        }
    }
}

// MARK: - Bauhaus design tokens

/// Single source of truth for the Bauhaus visual language: exact palette,
/// the Futura font helper, and spacing/rule constants. Inline (no new files).
enum BauhausTheme {
    // Palette (exact hex from the design spec).
    static let red    = Color(hex: 0xE2231A)   // "Again" rating; header circle
    static let yellow = Color(hex: 0xF2C200)   // "Hard" rating; header triangle
    static let green  = Color(hex: 0x2E9E4F)   // "Good" rating; correct highlight
    static let blue   = Color(hex: 0x1E52A8)   // "Easy" rating; header square
    static let ink    = Color(hex: 0x1A1A1A)   // text, rules, borders, Show Answer bar
    static let paper  = Color(hex: 0xF5F1E6)   // app + card background

    // Rules / spacing.
    static let headerRule: CGFloat = 3   // ink rule under the header
    static let rowRule: CGFloat    = 3   // ink rule above button rows
    static let buttonGap: CGFloat  = 2   // ink gaps between rating buttons
    static let pad: CGFloat        = 16  // standard screen padding

    /// Futura with the spec's fallback stack. iOS resolves "Futura" natively;
    /// `Font.custom` falls back through the system if unavailable.
    static func futura(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        Font.custom("Futura", size: size).weight(weight)
    }
}

extension Color {
    /// Build a Color from a 0xRRGGBB literal.
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue:  Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}

// MARK: - Bauhaus button style

/// Flat, hard-edged, full-bleed block button: solid fill, zero corner radius,
/// uppercase letter-spaced Futura white label. Used for both the Show Answer
/// bar (ink fill) and the rating buttons (per-spectrum color fill).
private struct BauhausBlockButtonStyle: ButtonStyle {
    let fill: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(maxWidth: .infinity)
            .padding(.vertical, 18)
            .background(fill)
            // Hard corners: no cornerRadius, no shadow, no gloss.
            .opacity(configuration.isPressed ? 0.82 : 1)
            .contentShape(Rectangle())
    }
}

// MARK: - Shared marks & tabs

/// The Bauhaus geometric mark: red circle + blue square + yellow triangle.
private struct BauhausMark: View {
    var size: CGFloat = 22

    var body: some View {
        HStack(spacing: size * 0.28) {
            Circle()
                .fill(BauhausTheme.red)
                .frame(width: size, height: size)
            Rectangle()
                .fill(BauhausTheme.blue)
                .frame(width: size, height: size)
            Triangle()
                .fill(BauhausTheme.yellow)
                .frame(width: size, height: size)
        }
    }
}

/// A hard-edged upward triangle.
private struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.midX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

/// Solid ink tab label (uppercase, letter-spaced Futura on paper text).
private struct InkTab: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(BauhausTheme.futura(size: 13, weight: .bold))
            .tracking(2)
            .foregroundColor(BauhausTheme.paper)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(BauhausTheme.ink)
    }
}

struct ContentView: View {
    @StateObject private var vm = ReviewViewModel()
    @State private var showingSyncSheet = false
    @State private var showingScores = false
    // Auto-grade answer bar: collapsed to just the AI recommendation until the
    // student taps it to reveal the four manual override buttons.
    @State private var overrideExpanded = false

    var body: some View {
        VStack(spacing: 0) {
            header
            content
        }
        .background(BauhausTheme.paper.ignoresSafeArea())
        .preferredColorScheme(.light)
        .onAppear { if case .loading = vm.phase { vm.start() } }
        .sheet(isPresented: $showingSyncSheet) {
            SyncSettingsView(vm: vm)
        }
        .sheet(isPresented: $showingScores) {
            ScoresView(scores: vm.scores)
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            // Left: geometric mark + GMAT wordmark.
            HStack(spacing: 8) {
                BauhausMark(size: 15)
                Text("GMAT")
                    .font(BauhausTheme.futura(size: 18, weight: .bold))
                    .tracking(1)
                    .foregroundColor(BauhausTheme.ink)
                    .fixedSize()
            }

            Spacer()

            // Scores button: opens the three-score panel (memory / performance
            // / readiness). Ink chip, beside the blue SYNC chip.
            Button {
                showingScores = true
            } label: {
                Text("SCORES")
                    .font(BauhausTheme.futura(size: 11, weight: .bold))
                    .tracking(1)
                    .fixedSize()
                    .foregroundColor(BauhausTheme.paper)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 5)
                    .background(BauhausTheme.ink)
            }
            .padding(.trailing, 6)

            // Sync: a single tap syncs immediately with the saved credentials.
            // The server/username/password sheet only opens when nothing is
            // saved yet, or on a double-tap (to change the server or account).
            VStack(alignment: .trailing, spacing: 2) {
                Text(vm.isSyncing ? "SYNCING…" : "SYNC")
                    .font(BauhausTheme.futura(size: 11, weight: .bold))
                    .tracking(1)
                    .fixedSize()
                    .foregroundColor(BauhausTheme.paper)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 5)
                    .background(BauhausTheme.blue)
            }
            .contentShape(Rectangle())
            // count:2 declared first so SwiftUI can disambiguate the double-tap.
            .onTapGesture(count: 2) { showingSyncSheet = true }
            .onTapGesture {
                if vm.hasCredentials { vm.syncNow() } else { showingSyncSheet = true }
            }
            .allowsHitTesting(!vm.isSyncing)
            .padding(.trailing, 8)

            // Right: answered count (tabular) with an uppercase ANSWERED label.
            VStack(alignment: .trailing, spacing: 0) {
                Text("\(vm.answeredCount)")
                    .font(BauhausTheme.futura(size: 24, weight: .bold).monospacedDigit())
                    .foregroundColor(BauhausTheme.ink)
                Text("ANSWERED")
                    .font(BauhausTheme.futura(size: 10, weight: .bold))
                    .tracking(1)
                    .fixedSize()
                    .foregroundColor(BauhausTheme.ink)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(BauhausTheme.paper)
        // 3px ink rule as the header's bottom border.
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(BauhausTheme.ink)
                .frame(height: BauhausTheme.headerRule)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.phase {
        case .loading:
            VStack(spacing: 20) {
                BauhausMark(size: 34)
                Text("LOADING DECK…")
                    .font(BauhausTheme.futura(size: 15, weight: .bold))
                    .tracking(3)
                    .foregroundColor(BauhausTheme.ink)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(BauhausTheme.paper)

        case .reviewing:
            if let card = vm.card {
                reviewer(for: card)
            } else {
                VStack(spacing: 20) {
                    BauhausMark(size: 34)
                    Text("LOADING DECK…")
                        .font(BauhausTheme.futura(size: 15, weight: .bold))
                        .tracking(3)
                        .foregroundColor(BauhausTheme.ink)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(BauhausTheme.paper)
            }

        case .finished:
            VStack(spacing: 24) {
                // Bold geometric composition.
                ZStack {
                    Rectangle()
                        .fill(BauhausTheme.blue)
                        .frame(width: 96, height: 96)
                    Circle()
                        .fill(BauhausTheme.red)
                        .frame(width: 54, height: 54)
                        .offset(x: 34, y: -34)
                    Triangle()
                        .fill(BauhausTheme.yellow)
                        .frame(width: 48, height: 48)
                        .offset(x: -34, y: 36)
                }
                Text("SESSION COMPLETE")
                    .font(BauhausTheme.futura(size: 22, weight: .bold))
                    .tracking(3)
                    .foregroundColor(BauhausTheme.ink)
                VStack(spacing: 2) {
                    Text("\(vm.answeredCount)")
                        .font(BauhausTheme.futura(size: 40, weight: .bold).monospacedDigit())
                        .foregroundColor(BauhausTheme.ink)
                    Text("ANSWERED")
                        .font(BauhausTheme.futura(size: 11, weight: .bold))
                        .tracking(2)
                        .foregroundColor(BauhausTheme.ink)
                }

                // Calibration + pacing recap — the session's payload.
                HStack(alignment: .top, spacing: 20) {
                    calibrationStat(
                        vm.confidentCount > 0 ? "\(vm.confidentCorrect)/\(vm.confidentCount)" : "–",
                        "CONFIDENT\n& RIGHT")
                    calibrationStat("\(vm.overconfidentMisses)", "OVER-\nCONFIDENT MISS")
                    calibrationStat("\(vm.skips)", "FLAGGED\n& SKIPPED")
                    calibrationStat("\(vm.slowCount)", "OVER\n2:00")
                }
                .padding(.top, 6)
            }
            .padding(BauhausTheme.pad)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(BauhausTheme.paper)

        case .error(let msg):
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    InkTab(text: "ERROR")
                    Text("SOMETHING WENT WRONG")
                        .font(BauhausTheme.futura(size: 18, weight: .bold))
                        .tracking(2)
                        .foregroundColor(BauhausTheme.ink)
                    // Keep the monospaced, selectable error text for debuggability.
                    Text(msg)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundColor(BauhausTheme.ink)
                        .textSelection(.enabled)
                }
                .padding(BauhausTheme.pad)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(BauhausTheme.paper)
        }
    }

    private func reviewer(for card: RenderedCard) -> some View {
        // Auto-grade only for single-answer multiple-choice cards; everything
        // else (free-response, two-part, passages) falls back to the manual
        // Show Answer → rating flow.
        let auto = vm.autoGradeEnabled && vm.cardIsMultipleChoice(card)
        return VStack(spacing: 0) {
            CardWebView(
                bodyHTML: vm.showingAnswer ? card.answerHTML : card.questionHTML,
                css: card.css,
                interactive: !vm.showingAnswer && !vm.awaitingConfidence && auto,
                onChoiceTap: { vm.handleChoiceTap($0) }
            )
            .id("\(card.cardId)-\(vm.showingAnswer)-\(auto)")  // force reload on change
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if !auto {
                // Manual flow: original Show Answer → four rating buttons.
                if vm.showingAnswer { ratingRow } else { showAnswerBar }
            } else if vm.showingAnswer, let rating = vm.autoRating {
                autoRatingBar(rating)
            } else if vm.awaitingConfidence {
                confidenceBar
            } else {
                tapToAnswerBar
            }
        }
    }

    /// Question sub-state: prompt to tap a choice, plus the pacing "cut your
    /// losses" move — Flag & skip.
    private var tapToAnswerBar: some View {
        VStack(spacing: 0) {
            Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.rowRule)
            HStack(spacing: BauhausTheme.buttonGap) {
                Text("TAP YOUR ANSWER")
                    .font(BauhausTheme.futura(size: 14, weight: .bold))
                    .tracking(2)
                    .foregroundColor(BauhausTheme.paper)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(BauhausTheme.ink)
                Button { vm.flagSkip() } label: {
                    Text("FLAG & SKIP")
                        .font(BauhausTheme.futura(size: 13, weight: .bold))
                        .tracking(1)
                        .foregroundColor(.white)
                }
                .buttonStyle(BauhausBlockButtonStyle(fill: BauhausTheme.red))
            }
            .background(BauhausTheme.ink)
        }
    }

    /// After a choice is tapped: ask for confidence (this both grades the card and
    /// trains the "should I keep working or move on" judgment).
    private var confidenceBar: some View {
        VStack(spacing: 0) {
            Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.rowRule)
            Text("YOU PICKED \(vm.pickedLetter ?? "?") · HOW SURE ARE YOU?")
                .font(BauhausTheme.futura(size: 13, weight: .bold))
                .tracking(1.5)
                .foregroundColor(BauhausTheme.paper)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(BauhausTheme.ink)
            HStack(spacing: BauhausTheme.buttonGap) {
                confidenceButton(.guessing, "GUESSING", BauhausTheme.red)
                confidenceButton(.fairlySure, "FAIRLY SURE", BauhausTheme.yellow)
                confidenceButton(.confident, "CONFIDENT", BauhausTheme.green)
            }
            .background(BauhausTheme.ink)
        }
    }

    private func confidenceButton(_ c: Confidence, _ label: String, _ color: Color) -> some View {
        Button { vm.submitConfidence(c) } label: {
            Text(label)
                .font(BauhausTheme.futura(size: 12, weight: .bold))
                .tracking(0.5)
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(BauhausBlockButtonStyle(fill: color))
    }

    /// The response-time + calibration line shown on reveal (time is *reported*,
    /// never used to grade; the flag calls out the overconfident miss).
    private var calibrationNote: String {
        let s = max(0, Int(vm.lastElapsed.rounded()))
        let t = String(format: "%d:%02d", s / 60, s % 60)
        let pace = s > 120 ? " · OVER 2:00" : ""
        return vm.lastOverconfident ? "⚠ CONFIDENT BUT WRONG · \(t)\(pace)" : "TIME \(t)\(pace)"
    }

    /// Answer sub-state (auto-grade on): a calibration/pace line, then just the
    /// engine's recommendation + NEXT. Tapping the AI recommendation reveals the
    /// four manual buttons (AI's pick marked) so the student can override — kept
    /// collapsed by default so the bottom of the screen stays calm.
    private func autoRatingBar(_ rating: Rating) -> some View {
        VStack(spacing: 0) {
            Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.rowRule)
            Text(calibrationNote)
                .font(BauhausTheme.futura(size: 12, weight: .bold))
                .tracking(1)
                .foregroundColor(.white)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(vm.lastOverconfident ? BauhausTheme.red : BauhausTheme.ink)

            if overrideExpanded {
                // Expanded: choose any rating. The engine's pick is marked "AI".
                HStack(spacing: BauhausTheme.buttonGap) {
                    overrideButton(.again, BauhausTheme.red,    aiPick: rating)
                    overrideButton(.hard,  BauhausTheme.yellow, aiPick: rating)
                    overrideButton(.good,  BauhausTheme.green,  aiPick: rating)
                    overrideButton(.easy,  BauhausTheme.blue,   aiPick: rating)
                }
                .background(BauhausTheme.ink)
            } else {
                // Collapsed: the AI recommendation (tap to change) + NEXT to accept.
                HStack(spacing: BauhausTheme.buttonGap) {
                    Button {
                        withAnimation(.easeOut(duration: 0.12)) { overrideExpanded = true }
                    } label: {
                        Text("AI · \(rating.label.uppercased())  ▾")
                            .font(BauhausTheme.futura(size: 15, weight: .bold))
                            .tracking(2)
                            .foregroundColor(.white)
                    }
                    .buttonStyle(BauhausBlockButtonStyle(fill: color(for: rating)))

                    Button { overrideExpanded = false; vm.answer(rating) } label: {
                        Text("NEXT →")
                            .font(BauhausTheme.futura(size: 15, weight: .bold))
                            .tracking(2)
                            .foregroundColor(.white)
                    }
                    .buttonStyle(BauhausBlockButtonStyle(fill: BauhausTheme.ink))
                }
                .background(BauhausTheme.ink)
            }
        }
    }

    /// One override button: like a rating button, but the engine's recommended
    /// rating carries a small "AI" mark so the student can spot it at a glance.
    private func overrideButton(_ rating: Rating, _ color: Color, aiPick: Rating) -> some View {
        Button {
            overrideExpanded = false
            vm.answer(rating)
        } label: {
            Text(rating.label.uppercased())
                .font(BauhausTheme.futura(size: 15, weight: .bold))
                .tracking(2)
                .foregroundColor(.white)
        }
        .buttonStyle(BauhausBlockButtonStyle(fill: color))
        .overlay(alignment: .top) {
            if rating.label == aiPick.label {
                Text("AI")
                    .font(BauhausTheme.futura(size: 8, weight: .bold))
                    .tracking(1)
                    .foregroundColor(.white)
                    .padding(.top, 3)
            }
        }
    }

    /// Bauhaus color for a rating (matches the manual rating buttons).
    private func color(for rating: Rating) -> Color {
        switch rating {
        case .again: return BauhausTheme.red
        case .hard:  return BauhausTheme.yellow
        case .good:  return BauhausTheme.green
        case .easy:  return BauhausTheme.blue
        }
    }

    /// One stat block in the finished-screen calibration/pacing recap.
    private func calibrationStat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 3) {
            Text(value)
                .font(BauhausTheme.futura(size: 20, weight: .bold).monospacedDigit())
                .foregroundColor(BauhausTheme.ink)
            Text(label)
                .font(BauhausTheme.futura(size: 9, weight: .bold))
                .tracking(1)
                .foregroundColor(BauhausTheme.ink)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
    }

    /// Question sub-state: full-width ink SHOW ANSWER bar with a 3px ink rule
    /// directly above it.
    private var showAnswerBar: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(BauhausTheme.ink)
                .frame(height: BauhausTheme.rowRule)

            Button {
                vm.showingAnswer = true
            } label: {
                Text("SHOW ANSWER")
                    .font(BauhausTheme.futura(size: 16, weight: .bold))
                    .tracking(3)
                    .foregroundColor(BauhausTheme.paper)
            }
            .buttonStyle(BauhausBlockButtonStyle(fill: BauhausTheme.ink))
        }
    }

    /// Answer sub-state: four rating buttons in one row with 2px ink gaps
    /// (ink background shows through), and a 3px ink rule across the top.
    private var ratingRow: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(BauhausTheme.ink)
                .frame(height: BauhausTheme.rowRule)

            // Ink background behind the row so the 2px gaps read as ink lines.
            HStack(spacing: BauhausTheme.buttonGap) {
                ratingButton(.again, BauhausTheme.red)
                ratingButton(.hard,  BauhausTheme.yellow)
                ratingButton(.good,  BauhausTheme.green)
                ratingButton(.easy,  BauhausTheme.blue)
            }
            .background(BauhausTheme.ink)
        }
    }

    private func ratingButton(_ rating: Rating, _ color: Color) -> some View {
        Button {
            vm.answer(rating)
        } label: {
            // White label on ALL four buttons, including yellow Hard (deck
            // owner's explicit decision — do NOT switch to black text).
            Text(rating.label.uppercased())
                .font(BauhausTheme.futura(size: 15, weight: .bold))
                .tracking(2)
                .foregroundColor(.white)
        }
        .buttonStyle(BauhausBlockButtonStyle(fill: color))
    }
}

// MARK: - Scores panel

/// The three-score panel: memory, performance, readiness — each with its range,
/// or the give-up state (what's still missing). Bauhaus idiom, matching the app.
private struct ScoresView: View {
    let scores: Scores?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("GMAT SCORES")
                    .font(BauhausTheme.futura(size: 18, weight: .bold))
                    .tracking(3)
                    .foregroundColor(BauhausTheme.ink)
                Spacer()
                Button { dismiss() } label: {
                    Text("DONE")
                        .font(BauhausTheme.futura(size: 13, weight: .bold))
                        .tracking(2)
                        .foregroundColor(BauhausTheme.ink)
                }
            }
            .padding(BauhausTheme.pad)
            .overlay(alignment: .bottom) {
                Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.headerRule)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let s = scores {
                        ScoreBlock(label: "MEMORY", accent: BauhausTheme.green, value: s.memory)
                        Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.rowRule)
                        ScoreBlock(label: "PERFORMANCE", accent: BauhausTheme.yellow, value: s.performance)
                        Rectangle().fill(BauhausTheme.ink).frame(height: BauhausTheme.rowRule)
                        ScoreBlock(label: "READINESS", accent: BauhausTheme.blue, value: s.readiness)
                    } else {
                        Text("SCORES NOT LOADED YET")
                            .font(BauhausTheme.futura(size: 14, weight: .bold))
                            .tracking(2)
                            .foregroundColor(BauhausTheme.ink)
                            .padding(BauhausTheme.pad)
                    }
                }
            }
        }
        .background(BauhausTheme.paper.ignoresSafeArea())
        .preferredColorScheme(.light)
    }
}

/// A single score row: big number + range when scored, or "NOT ENOUGH DATA YET"
/// plus the missing-data checklist (the give-up rule) when abstaining.
private struct ScoreBlock: View {
    let label: String
    let accent: Color
    let value: ScoreValue

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Rectangle().fill(accent).frame(width: 14, height: 14)
                Text(label)
                    .font(BauhausTheme.futura(size: 13, weight: .bold))
                    .tracking(2)
                    .foregroundColor(BauhausTheme.ink)
            }

            if value.abstained {
                Text("NOT ENOUGH DATA YET")
                    .font(BauhausTheme.futura(size: 20, weight: .bold))
                    .foregroundColor(BauhausTheme.ink)
                ForEach(value.missing, id: \.self) { m in
                    Text("— \(m)")
                        .font(BauhausTheme.futura(size: 13))
                        .foregroundColor(BauhausTheme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text(headline)
                    .font(BauhausTheme.futura(size: 30, weight: .bold).monospacedDigit())
                    .foregroundColor(BauhausTheme.ink)
                Text(subline)
                    .font(BauhausTheme.futura(size: 13))
                    .foregroundColor(BauhausTheme.ink)
                ForEach(value.reasons, id: \.self) { r in
                    Text("· \(r)")
                        .font(BauhausTheme.futura(size: 12))
                        .foregroundColor(BauhausTheme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(BauhausTheme.pad)
    }

    /// "72 / 100" for pct scores; "545" for the GMAT-scale readiness.
    private var headline: String {
        let n = Int(value.score.rounded())
        return value.unit == "gmat" ? "\(n)" : "\(n) / 100"
    }

    /// The likely range (+ confidence for readiness).
    private var subline: String {
        let lo = Int(value.low.rounded())
        let hi = Int(value.high.rounded())
        var s = "range \(lo)–\(hi)"
        if !value.confidence.isEmpty { s += " · confidence \(value.confidence)" }
        return s
    }
}

// MARK: - Sync settings sheet

/// Server URL + credentials + a manual Sync trigger, in the Bauhaus idiom:
/// flat ink/paper fields with hard edges, uppercase letter-spaced labels.
private struct SyncSettingsView: View {
    @ObservedObject var vm: ReviewViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Sheet header: ink rule + wordmark, matching the app header.
            HStack {
                Text("SYNC SETTINGS")
                    .font(BauhausTheme.futura(size: 18, weight: .bold))
                    .tracking(3)
                    .foregroundColor(BauhausTheme.ink)
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Text("DONE")
                        .font(BauhausTheme.futura(size: 13, weight: .bold))
                        .tracking(2)
                        .foregroundColor(BauhausTheme.ink)
                }
            }
            .padding(BauhausTheme.pad)
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(BauhausTheme.ink)
                    .frame(height: BauhausTheme.headerRule)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    fieldBlock(label: "Auto-grade answers (AI)") {
                        Toggle(isOn: $vm.autoGradeEnabled) {
                            Text(vm.autoGradeEnabled
                                 ? "On — tap a choice and the engine sets Again/Hard/Good/Easy."
                                 : "Off — Show Answer, then rate it yourself.")
                                .font(BauhausTheme.futura(size: 13, weight: .medium))
                                .foregroundColor(BauhausTheme.ink)
                        }
                        .tint(BauhausTheme.green)
                    }

                    fieldBlock(label: "Server URL (needs trailing slash)") {
                        TextField("https://example.trycloudflare.com/", text: $vm.serverURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled(true)
                            .keyboardType(.URL)
                    }

                    fieldBlock(label: "Username") {
                        TextField("username", text: $vm.syncUser)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled(true)
                    }

                    fieldBlock(label: "Password") {
                        SecureField("password", text: $vm.syncPass)
                    }

                    if !vm.syncStatus.isEmpty {
                        Text(vm.syncStatus.uppercased())
                            .font(BauhausTheme.futura(size: 12, weight: .bold))
                            .tracking(1.5)
                            .foregroundColor(BauhausTheme.ink)
                            .textSelection(.enabled)
                    }

                    Button {
                        vm.syncNow()
                    } label: {
                        Text(vm.isSyncing ? "SYNCING…" : "SYNC NOW")
                            .font(BauhausTheme.futura(size: 16, weight: .bold))
                            .tracking(3)
                            .foregroundColor(BauhausTheme.paper)
                    }
                    .buttonStyle(BauhausBlockButtonStyle(fill: BauhausTheme.ink))
                    .disabled(vm.isSyncing || vm.serverURL.isEmpty)
                }
                .padding(BauhausTheme.pad)
            }
        }
        .background(BauhausTheme.paper.ignoresSafeArea())
        .preferredColorScheme(.light)
    }

    /// Uppercase ink label above a flat, hard-edged paper-on-ink text field.
    @ViewBuilder
    private func fieldBlock<Content: View>(label: String, @ViewBuilder field: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased())
                .font(BauhausTheme.futura(size: 11, weight: .bold))
                .tracking(1.5)
                .foregroundColor(BauhausTheme.ink)
            field()
                .font(BauhausTheme.futura(size: 15))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(BauhausTheme.paper)
                .overlay(
                    Rectangle()
                        .stroke(BauhausTheme.ink, lineWidth: 2)
                )
        }
    }
}
