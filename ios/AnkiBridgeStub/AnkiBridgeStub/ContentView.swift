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
    @Published var card: RenderedCard?
    @Published var showingAnswer = false
    @Published var answeredCount = 0
    /// The three GMAT scores, refreshed after each card / sync. nil until first load.
    @Published var scores: Scores?

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
                }
            } catch {
                await MainActor.run { self.phase = .error("\(error)") }
            }
        }
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
            HStack(spacing: 10) {
                BauhausMark(size: 22)
                Text("GMAT")
                    .font(BauhausTheme.futura(size: 24, weight: .bold))
                    .tracking(4)
                    .foregroundColor(BauhausTheme.ink)
            }

            Spacer()

            // Scores button: opens the three-score panel (memory / performance
            // / readiness). Ink chip, beside the blue SYNC chip.
            Button {
                showingScores = true
            } label: {
                Text("SCORES")
                    .font(BauhausTheme.futura(size: 13, weight: .bold))
                    .tracking(2)
                    .foregroundColor(BauhausTheme.paper)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(BauhausTheme.ink)
            }
            .padding(.trailing, 10)

            // Sync button: opens the server-settings sheet, and doubles as a
            // status readout via the ink label underneath.
            Button {
                showingSyncSheet = true
            } label: {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(vm.isSyncing ? "SYNCING…" : "SYNC")
                        .font(BauhausTheme.futura(size: 13, weight: .bold))
                        .tracking(2)
                        .foregroundColor(BauhausTheme.paper)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(BauhausTheme.blue)
                }
            }
            .disabled(vm.isSyncing)
            .padding(.trailing, 14)

            // Right: answered count (tabular) with an uppercase ANSWERED label.
            VStack(alignment: .trailing, spacing: 0) {
                Text("\(vm.answeredCount)")
                    .font(BauhausTheme.futura(size: 28, weight: .bold).monospacedDigit())
                    .foregroundColor(BauhausTheme.ink)
                Text("ANSWERED")
                    .font(BauhausTheme.futura(size: 10, weight: .bold))
                    .tracking(2)
                    .foregroundColor(BauhausTheme.ink)
            }
        }
        .padding(.horizontal, BauhausTheme.pad)
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
        VStack(spacing: 0) {
            CardWebView(
                bodyHTML: vm.showingAnswer ? card.answerHTML : card.questionHTML,
                css: card.css
            )
            .id("\(card.cardId)-\(vm.showingAnswer)")  // force reload on change
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if vm.showingAnswer {
                ratingRow
            } else {
                showAnswerBar
            }
        }
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
