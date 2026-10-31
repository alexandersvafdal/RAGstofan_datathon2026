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
                        .contentShape(.contextMenuPreview, RoundedRectangle(cornerRadius: 20, style: .continuous))
                        .copyable(ex.question, ex.lang)
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
/// Untouched, they are the server's defaults (the best model, the longest thinking); a changed one is tinted.
private struct Tuning: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal

    var body: some View {
        let isl = model.ui == .is
        let names = isl ? ["none": "Engin", "low": "Stutt", "medium": "Miðlungs", "high": "Löng"]
                        : ["none": "Off", "low": "Short", "medium": "Medium", "high": "Long"]
        let levels = model.models?.reasoning ?? ["none", "low", "medium", "high"]
        let best = model.models?.default
        let longest = model.models?.reasoningDefault ?? "high"
        let temp = model.temperatureChoice ?? 0
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Menu {
                    Picker(isl ? "Líkan" : "Model", selection: Binding(get: { model.activeModel ?? "" },
                                                                        set: { model.modelChoice = $0 == best ? nil : $0 })) {
                        ForEach(model.models?.models ?? [], id: \.self) { m in
                            Label(m == best ? "\(m) (\(isl ? "besta" : "best"))" : m, systemImage: m == best ? "star.fill" : "cpu").tag(m)
                        }
                    }
                } label: {
                    chip(changed: model.modelChoice != nil) {
                        Image(systemName: model.modelChoice == nil ? "sparkles" : "cpu")
                            .contentTransition(.symbolEffect(.replace))
                        Text(model.activeModel ?? "–").contentTransition(.interpolate)
                    }
                }
                Menu {
                    Picker(isl ? "Hugsun" : "Thinking", selection: Binding(get: { model.activeReasoning },
                                                                            set: { model.reasoningChoice = $0 == longest ? nil : $0 })) {
                        ForEach(levels, id: \.self) { Text(names[$0] ?? $0).tag($0) }
                    }
                } label: {
                    chip(changed: model.reasoningChoice != nil) {
                        Meter(level: levels.firstIndex(of: model.activeReasoning) ?? 0, of: levels.count - 1)
                        Text("\(isl ? "Hugsun" : "Thinking") ")
                        + Text(names[model.activeReasoning] ?? model.activeReasoning).fontWeight(.semibold)
                    }
                }
                Menu {
                    Picker(isl ? "Hitastig" : "Temperature", selection: Binding(get: { model.temperatureChoice ?? 0 },
                                                                                 set: { model.temperatureChoice = $0 == 0 ? nil : $0 })) {
                        ForEach([0.0, 0.3, 0.7, 1.0], id: \.self) { t in
                            Text(t == 0 ? (isl ? "0 · sama svar alltaf" : "0 · same answer every time") : String(format: "%.1f", t)).tag(t)
                        }
                    }
                } label: {
                    chip(changed: model.temperatureChoice != nil) {
                        // cool at 0, warmer as it rises
                        Circle()
                            .fill(LinearGradient(colors: [pal.series1, pal.series2], startPoint: .bottom, endPoint: .top))
                            .frame(width: 9, height: 9)
                            .hueRotation(.degrees(temp * 40))
                            .scaleEffect(0.8 + temp * 0.4)
                        Text(String(format: "%.1f", temp)).monospacedDigit().contentTransition(.numericText(value: temp))
                    }
                }
            }
            .padding(.horizontal, 2)
            .padding(.vertical, 1)
        }
        .disabled(model.models == nil)
        .opacity(model.models == nil ? 0.5 : 1)
        .animation(.spring(duration: 0.4, bounce: 0.25), value: model.modelChoice)
        .animation(.spring(duration: 0.4, bounce: 0.25), value: model.reasoningChoice)
        .animation(.spring(duration: 0.4, bounce: 0.25), value: model.temperatureChoice)
        .sensoryFeedback(.selection, trigger: model.modelChoice)
        .sensoryFeedback(.selection, trigger: model.reasoningChoice)
        .sensoryFeedback(.selection, trigger: model.temperatureChoice)
    }

    private func chip<C: View>(changed: Bool, @ViewBuilder _ content: () -> C) -> some View {
        HStack(spacing: 6) {
            content()
            Image(systemName: "chevron.up.chevron.down").font(.system(size: 8, weight: .semibold)).opacity(0.6)
        }
        .font(.caption)
        .lineLimit(1)
        .foregroundStyle(changed ? pal.accent : pal.text2)
        .padding(.horizontal, 11)
        .padding(.vertical, 7)
        .background(changed ? pal.soft : pal.card, in: Capsule())
        .overlay(Capsule().stroke(changed ? pal.accent.opacity(0.35) : pal.text.opacity(0.1)))
        .shadow(color: .black.opacity(0.04), radius: 4, y: 2)
    }
}

/// Thinking length as rising bars: none lit for Off, all four for Long.
private struct Meter: View {
    @Environment(\.pal) private var pal
    let level: Int
    let of: Int
    var body: some View {
        HStack(alignment: .bottom, spacing: 1.5) {
            ForEach(0..<max(of, 1), id: \.self) { i in
                Capsule()
                    .fill(i < level ? pal.accent : pal.text.opacity(0.2))
                    .frame(width: 2.5, height: 4 + CGFloat(i) * 2.5)
            }
        }
        .frame(height: 12, alignment: .bottom)
    }
}
