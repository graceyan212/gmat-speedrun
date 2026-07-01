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

    private let engine = AnkiEngine()

    func start() {
        // rslib work is synchronous + CPU-bound; run off the main thread, then
        // publish results back on main.
        Task.detached(priority: .userInitiated) { [engine] in
            do {
                try engine.startSession()
                let first = try engine.nextCard()
                await MainActor.run {
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
                await MainActor.run {
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
}

struct ContentView: View {
    @StateObject private var vm = ReviewViewModel()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
        }
        .onAppear { if case .loading = vm.phase { vm.start() } }
    }

    private var header: some View {
        HStack {
            Text("GMAT Review")
                .font(.headline)
            Spacer()
            Text("Answered: \(vm.answeredCount)")
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding()
    }

    @ViewBuilder
    private var content: some View {
        switch vm.phase {
        case .loading:
            VStack(spacing: 12) {
                ProgressView()
                Text("Loading GMAT deck via rslib…")
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .reviewing:
            if let card = vm.card {
                reviewer(for: card)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }

        case .finished:
            VStack(spacing: 12) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.green)
                Text("Session complete")
                    .font(.title3).bold()
                Text("Answered \(vm.answeredCount) cards on the shared Anki engine.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity)

        case .error(let msg):
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Error").font(.headline).foregroundStyle(.red)
                    Text(msg)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                }
                .padding()
            }
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

            Divider()

            if vm.showingAnswer {
                HStack(spacing: 8) {
                    ratingButton(.again, .red)
                    ratingButton(.hard, .orange)
                    ratingButton(.good, .green)
                    ratingButton(.easy, .blue)
                }
                .padding()
            } else {
                Button {
                    vm.showingAnswer = true
                } label: {
                    Text("Show Answer")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .padding()
            }
        }
    }

    private func ratingButton(_ rating: Rating, _ color: Color) -> some View {
        Button {
            vm.answer(rating)
        } label: {
            Text(rating.label)
                .font(.subheadline.bold())
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
        }
        .buttonStyle(.borderedProminent)
        .tint(color)
    }
}
