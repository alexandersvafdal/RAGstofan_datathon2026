// The one screen: the home page until the first question, then the conversation, with the question box at the bottom.
import SwiftUI

typealias Ask = (String, String?) -> Void

struct ChatView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @State private var draft = ""
    @State private var showSettings = false
    @State private var showHistory = false
    @FocusState private var focused: Bool

    var body: some View {
        let w = Words(lang: model.ui)
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 20) {
                        if model.exchanges.isEmpty {
                            HomeView(ask: send, openSettings: { showSettings = true })
                                .transition(.opacity.combined(with: .scale(scale: 0.97)))
                        } else {
                            ForEach(model.exchanges) { ex in
                                ExchangeView(ex: ex, ask: send).id(ex.id)
                            }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 24)
                    .frame(maxWidth: 760)
                    .frame(maxWidth: .infinity)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: model.exchanges.count) {
                    guard let last = model.exchanges.last else { return }
                    withAnimation(.smooth) { proxy.scrollTo(last.id, anchor: .top) }
                }
            }
            .background(pal.bg.ignoresSafeArea())
            .safeAreaInset(edge: .bottom) { composer(w) }
            .navigationTitle("RAGstofan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(pal.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showHistory = true } label: { Image(systemName: "clock.arrow.circlepath") }
                        .accessibilityLabel(model.ui == .is ? "Fyrri samtöl" : "Past conversations")
                }
                ToolbarItem(placement: .topBarLeading) {
                    if !model.exchanges.isEmpty {
                        Button { withAnimation(.smooth) { model.reset() } } label: { Image(systemName: "square.and.pencil") }
                            .accessibilityLabel(w.newChat)
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "slider.horizontal.3") }
                        .accessibilityLabel(w.settings)
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView().environment(model).environment(\.pal, pal).tint(pal.accent)
            }
            .sheet(isPresented: $showHistory) {
                HistoryView().environment(model).environment(\.pal, pal).tint(pal.accent)
            }
        }
    }

    private func composer(_ w: Words) -> some View {
        let empty = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(spacing: 8) {
          Tuning()
          HStack(spacing: 10) {
            TextField(w.placeholder, text: $draft)
                .focused($focused)
                .submitLabel(.send)
                .onSubmit { send(draft, nil) }
                .foregroundStyle(pal.text)
                .padding(.horizontal, 18)
                .frame(height: 48)
                .background(pal.card, in: Capsule())
                .overlay(Capsule().stroke(pal.text.opacity(0.1)))
            Button { send(draft, nil) } label: {
                ZStack {
                    if model.busy { ProgressView().tint(pal.accentInk) }
                    else { Image(systemName: "arrow.up").font(.system(size: 18, weight: .semibold)) }
                }
                .frame(width: 48, height: 48)
                .foregroundStyle(pal.accentInk)
                .background(pal.accent, in: Circle())
                .opacity(empty && !model.busy ? 0.45 : 1)
            }
            .disabled(model.busy || empty)
            .accessibilityLabel("Send")
          }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    private func send(_ question: String, _ tableId: String?) {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !model.busy else { return }
        draft = ""
        focused = false
        model.send(q, tableId: tableId)
    }
}

/// The user's bubble, then live progress, then the answer card (or the error and a retry).
struct ExchangeView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let ex: Exchange
    let ask: Ask

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if ex.tableId == nil {   // a clarification pick re-asks the same question, so no second bubble
                HStack {
                    Spacer(minLength: 48)
                    Text(ex.question)
                        .foregroundStyle(pal.accentInk)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .background(pal.accent, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                }
            }
            if let a = ex.answer {
                AnswerCard(a: a, ask: ask, fast: ex.fast)
                    .transition(.opacity.combined(with: .offset(y: 12)))
            } else if let err = ex.error {
                let w = Words(lang: ex.lang)
                Card {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("\(w.failed). \(err)").foregroundStyle(pal.text)
                        Button(w.retry) {
                            model.exchanges.removeAll { $0.id == ex.id }
                            model.send(ex.question, tableId: ex.tableId, fast: ex.fast)
                        }
                        .buttonStyle(PillStyle())
                    }
                }
            } else {
                LiveStepsView(ex: ex)
                    .transition(.opacity)
            }
        }
    }
}

/// How the next question is answered: model, thinking length and temperature, as small menus above the question box.
/// Untouched, they are the server's defaults: the best model and the longest thinking.
private struct Tuning: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal

    var body: some View {
        let isl = model.ui == .is
        let names = isl ? ["none": "Engin", "low": "Stutt", "medium": "Miðlungs", "high": "Löng"]
                        : ["none": "Off", "low": "Short", "medium": "Medium", "high": "Long"]
        let best = model.models?.default
        let longest = model.models?.reasoningDefault ?? "high"
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Menu {
                    Picker(isl ? "Líkan" : "Model", selection: Binding(get: { model.activeModel ?? "" },
                                                                        set: { model.modelChoice = $0 == best ? nil : $0 })) {
                        ForEach(model.models?.models ?? [], id: \.self) { Text($0 == best ? "\($0) (\(isl ? "besta" : "best"))" : $0).tag($0) }
                    }
                } label: { chip("cpu", model.activeModel ?? "–") }
                Menu {
                    Picker(isl ? "Hugsun" : "Thinking", selection: Binding(get: { model.activeReasoning },
                                                                            set: { model.reasoningChoice = $0 == longest ? nil : $0 })) {
                        ForEach(model.models?.reasoning ?? ["none", "low", "medium", "high"], id: \.self) { Text(names[$0] ?? $0).tag($0) }
                    }
                } label: { chip("brain", "\(isl ? "Hugsun" : "Thinking"): \(names[model.activeReasoning] ?? model.activeReasoning)") }
                Menu {
                    Picker(isl ? "Hitastig" : "Temperature", selection: Binding(get: { model.temperatureChoice ?? 0 },
                                                                                 set: { model.temperatureChoice = $0 == 0 ? nil : $0 })) {
                        ForEach([0.0, 0.3, 0.7, 1.0], id: \.self) { Text(String(format: "%.1f", $0)).tag($0) }
                    }
                } label: { chip("thermometer.medium", String(format: "%.1f", model.temperatureChoice ?? 0)) }
            }
            .padding(.horizontal, 2)
        }
        .disabled(model.models == nil)
    }

    private func chip(_ icon: String, _ text: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
            Text(text).lineLimit(1)
            Image(systemName: "chevron.up.chevron.down").font(.system(size: 8, weight: .semibold))
        }
        .font(.caption)
        .foregroundStyle(pal.text2)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(pal.card, in: Capsule())
        .overlay(Capsule().stroke(pal.text.opacity(0.1)))
    }
}
