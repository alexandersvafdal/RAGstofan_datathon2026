// The one screen: the home page until the first question, then the conversation, with the question box at the bottom.
import SwiftUI

typealias Ask = (String, String?) -> Void

struct ChatView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @State private var draft = ""
    @State private var fromVoice = false   // the draft is what the microphone heard, waiting for a look
    @State private var showSettings = false
    @State private var drawer = false
    @State private var tuning = false
    @FocusState private var focused: Bool

    var body: some View {
        let w = Words(lang: model.ui)
        ZStack(alignment: .leading) {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    // lazy: past answers (charts, tables) are built only when they scroll into view
                    LazyVStack(spacing: 20) {
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
            // how questions are answered: dropped down from the model name in the title
            .overlay(alignment: .top) {
                if tuning {
                    Color.black.opacity(0.28).ignoresSafeArea()
                        .onTapGesture { withAnimation(.spring(duration: 0.35)) { tuning = false } }
                        .transition(.opacity)
                    // scrolls only when the screen is too short for it (landscape)
                    ViewThatFits(in: .vertical) {
                        TuningPanel()
                        ScrollView { TuningPanel() }.scrollIndicators(.hidden)
                    }
                        .padding(.horizontal, 12)
                        .padding(.top, 6)
                        .frame(maxWidth: 520)
                        .transition(.asymmetric(insertion: .scale(scale: 0.92, anchor: .topLeading).combined(with: .opacity),
                                                removal: .scale(scale: 0.96, anchor: .topLeading).combined(with: .opacity)))
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(pal.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    HStack(spacing: 4) {
                        Button { focused = false; tuning = false; withAnimation(.spring(duration: 0.4, bounce: 0.1)) { drawer = true } } label: {
                            Image(systemName: "line.3.horizontal")
                        }
                        .accessibilityLabel(model.ui == .is ? "Valmynd og fyrri samtöl" : "Menu and past conversations")
                        ModelTitle(open: tuning) {
                            focused = false
                            withAnimation(.spring(duration: 0.4, bounce: 0.2)) { tuning.toggle() }
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { tuning = false; showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel(w.settings)
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView().environment(model).environment(\.pal, pal).tint(pal.accent)
            }
        }
        // the hamburger menu: past conversations, search, new conversation, private mode
        if drawer {
            Color.black.opacity(0.35).ignoresSafeArea()
                .onTapGesture { withAnimation(.spring(duration: 0.35)) { drawer = false } }
                .transition(.opacity)
            Drawer(close: { withAnimation(.spring(duration: 0.35)) { drawer = false } })
                .transition(.move(edge: .leading))
                .zIndex(1)
        }
        }
    }

    private func composer(_ w: Words) -> some View {
        let empty = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let voice = model.voice
        let isl = model.ui == .is
        return VStack(spacing: 8) {
          if let e = voice.error {
              Label(e, systemImage: "mic.slash").font(.caption).foregroundStyle(.red)
                  .onTapGesture { voice.error = nil }
                  .transition(.opacity)
          }
          // the answer being made into speech, or read: one bar with a stop, like a mini player
          if let id = voice.loadingSpeech ?? voice.speaking, !id.hasPrefix("sample-") {
              VoiceBar(loading: voice.loadingSpeech != nil, isl: isl) { voice.stopSpeaking() }
                  .transition(.move(edge: .bottom).combined(with: .opacity))
          }
          // what the microphone heard sits in the box for a look before it goes
          if fromVoice && !empty {
              HStack(spacing: 6) {
                  Image(systemName: "waveform").foregroundStyle(pal.accent)
                  Text(isl ? "Heyrði þetta. Lagaðu eða sendu." : "Heard this. Edit it or send.")
                  Spacer()
                  Button { draft = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(pal.text3) }
                      .accessibilityLabel(isl ? "Hreinsa" : "Clear")
              }
              .font(.caption).foregroundStyle(pal.text2)
              .padding(.horizontal, 6)
              .transition(.opacity)
          }
          HStack(spacing: 10) {
            ZStack {
                if voice.state == .idle {
                    TextField(w.placeholder, text: $draft)
                        .focused($focused)
                        .submitLabel(.send)
                        .onSubmit { send(draft, nil) }
                        .foregroundStyle(pal.text)
                        .padding(.horizontal, 18)
                        .transition(.opacity)
                } else {
                    // listening: the microphone's level, or a moment while Whisper writes it down
                    HStack(spacing: 12) {
                        if voice.state == .listening { LevelBars(level: voice.level) } else { ProgressView().tint(pal.accent) }
                        Text(voice.state == .listening ? (isl ? "Hlusta…" : "Listening…") : (isl ? "Skrifa upp…" : "Writing it down…"))
                            .font(.subheadline).foregroundStyle(pal.text2)
                            .contentTransition(.interpolate)
                        Spacer()
                        if voice.state == .listening {
                            Button { withAnimation(.snappy) { voice.cancelListening() } } label: {
                                Image(systemName: "xmark").font(.subheadline.weight(.semibold)).foregroundStyle(pal.text3)
                            }
                            .accessibilityLabel(isl ? "Hætta við" : "Cancel")
                        }
                    }
                    .padding(.horizontal, 18)
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }
            }
            .frame(height: 48)
            .background(pal.card, in: Capsule())
            .overlay(Capsule().stroke(voice.state == .listening ? pal.accent.opacity(0.6) : pal.text.opacity(0.1),
                                      lineWidth: voice.state == .listening ? 2 : 1))
            Button(action: mainButton) {
                ZStack {
                    if model.busy || voice.state == .transcribing { ProgressView().tint(pal.accentInk) }
                    else if voice.state == .listening { Image(systemName: "stop.fill").font(.system(size: 16, weight: .semibold)) }
                    else if empty { Image(systemName: "mic.fill").font(.system(size: 18, weight: .semibold)) }
                    else { Image(systemName: "arrow.up").font(.system(size: 18, weight: .semibold)) }
                }
                .contentTransition(.symbolEffect(.replace))
                .frame(width: 48, height: 48)
                .foregroundStyle(pal.accentInk)
                .background(pal.accent, in: Circle())
                .scaleEffect(voice.state == .listening ? 1 + voice.level * 0.12 : 1)
            }
            .disabled(model.busy || voice.state == .transcribing)
            .accessibilityLabel(voice.state == .listening ? (isl ? "Hætta að hlusta" : "Stop listening")
                                : empty ? (isl ? "Spyrja með rödd" : "Ask by voice") : "Send")
            .sensoryFeedback(.impact(weight: .medium), trigger: voice.state)
          }
          .animation(.spring(duration: 0.35, bounce: 0.2), value: voice.state)
          .animation(.spring(duration: 0.3), value: empty)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .animation(.spring(duration: 0.35, bounce: 0.15), value: voice.loadingSpeech ?? voice.speaking)
        .animation(.snappy, value: fromVoice && !empty)
        .onChange(of: draft) { if draft.isEmpty { fromVoice = false } }
    }

    /// One button: send what is typed, or start and stop listening when the box is empty.
    private func mainButton() {
        let voice = model.voice
        if voice.state == .listening {
            Task {
                // Whisper hears which language is spoken; the language setting is for the interface and typed
                // questions, and forcing it here turned Icelandic speech into English text
                // into the box, not straight off: the user reads what Whisper heard, fixes it, then sends
                if let heard = await voice.finishListening(api: model.apiClient, language: nil) {
                    draft = heard
                    fromVoice = true
                    focused = true
                }
            }
        } else if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            focused = false
            Task { await voice.startListening() }
        } else {
            send(draft, nil)
        }
    }

    private func send(_ question: String, _ tableId: String?) {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !model.busy else { return }
        let spoken = fromVoice           // asked by voice: the answer is read aloud too
        draft = ""
        fromVoice = false
        focused = false
        model.send(q, tableId: tableId, spoken: spoken)
    }
}

/// Speech on its way or playing: a spinner while the Spark makes it, dancing bars while it reads, and a stop.
private struct VoiceBar: View {
    @Environment(\.pal) private var pal
    let loading: Bool
    let isl: Bool
    let stop: () -> Void
    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                if loading { ProgressView().controlSize(.small).tint(pal.accent) }
                else { SpeakingBars() }
            }
            .frame(width: 28, height: 24)
            Text(loading ? (isl ? "Bý til röddina…" : "Preparing the voice…") : (isl ? "Les svarið…" : "Reading the answer…"))
                .font(.subheadline.weight(.medium)).foregroundStyle(pal.text)
                .contentTransition(.interpolate)
            Spacer()
            Button(action: stop) {
                Image(systemName: "stop.fill").font(.system(size: 12, weight: .bold))
                    .foregroundStyle(pal.accentInk)
                    .frame(width: 28, height: 28)
                    .background(pal.accent, in: Circle())
            }
            .accessibilityLabel(isl ? "Stöðva" : "Stop")
        }
        .padding(.leading, 14).padding(.trailing, 8).padding(.vertical, 8)
        .background(pal.soft, in: Capsule())
        .overlay(Capsule().stroke(pal.accent.opacity(0.25)))
        .animation(.snappy, value: loading)
    }
}

/// Five bars that sway on their own, the sign that the phone is talking.
struct SpeakingBars: View {
    @Environment(\.pal) private var pal
    var body: some View {
        TimelineView(.animation) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate
            HStack(spacing: 3) {
                ForEach(0..<5, id: \.self) { i in
                    let h = 0.35 + 0.65 * abs(sin(t * (2.6 + Double(i) * 0.7) + Double(i)))
                    Capsule().fill(pal.accent).frame(width: 3, height: 6 + 16 * h)
                }
            }
        }
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
                AnswerCard(a: a, ask: ask, fast: ex.fast, exchange: ex)
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

/// "qwen3.8:27b" -> "Qwen3.8:27B": how a model is named in the title and the picker.
func prettyModel(_ name: String) -> String {
    var n = name.prefix(1).uppercased() + name.dropFirst()
    if n.lowercased().hasPrefix("gpt-oss") { n = "GPT-OSS" + n.dropFirst(7) }
    if let r = n.range(of: #"(\d+(\.\d+)?)([bm])\b"#, options: .regularExpression) { n.replaceSubrange(r, with: n[r].uppercased()) }
    return n
}

/// The model in use as the screen's title, with a chevron: it opens the panel to pick a model.
/// A tinted dot says something differs from the recommended settings; the Private tag sits beside it.
private struct ModelTitle: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let open: Bool
    let action: () -> Void

    var body: some View {
        let changed = model.modelChoice != nil
        Button(action: action) {
            HStack(spacing: 5) {
                Text(model.activeModel.map(prettyModel) ?? "RAGstofan")
                    .font(.headline).foregroundStyle(pal.text)
                    .contentTransition(.interpolate)
                if changed { Circle().fill(pal.accent).frame(width: 6, height: 6).transition(.scale) }
                Image(systemName: "chevron.down")
                    .font(.caption.weight(.bold)).foregroundStyle(pal.text3)
                    .rotationEffect(.degrees(open ? 180 : 0))
                if model.privateMode {
                    Image(systemName: "eye.slash").font(.caption.weight(.semibold)).foregroundStyle(pal.accent)
                        .padding(5).background(pal.soft, in: Circle())
                        .accessibilityLabel(model.ui == .is ? "Einkahamur" : "Private mode")
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .lineLimit(1)
            .fixedSize()
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.spring(duration: 0.35), value: changed)
        .animation(.spring(duration: 0.35), value: model.privateMode)
        .animation(.spring(duration: 0.35, bounce: 0.3), value: open)
        .accessibilityLabel(model.ui == .is ? "Líkan og stillingar svara" : "Model and answer settings")
    }
}

/// The dropdown: the models in a layered card. The server's best is chosen until the user picks another, and is
/// tagged "Recommended"; one tap puts it back. Thinking is off and the temperature 0, set by the server.
private struct TuningPanel: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal

    var body: some View {
        let isl = model.ui == .is
        let best = model.models?.default
        let fast = model.models?.fast
        let changed = model.modelChoice != nil
        VStack(alignment: .leading, spacing: 0) {
            section(isl ? "Líkan" : "Model") {
                VStack(spacing: 2) {
                    ForEach(model.models?.models ?? [], id: \.self) { m in
                        let on = m == model.activeModel
                        Button { model.modelChoice = m == best ? nil : m } label: {
                            HStack(spacing: 12) {
                                Image(systemName: m == best ? "sparkles" : m == fast ? "hare" : "cpu")
                                    .font(.subheadline).frame(width: 22)
                                    .foregroundStyle(on ? pal.accent : pal.text3)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(prettyModel(m)).font(.subheadline.weight(on ? .semibold : .regular)).foregroundStyle(pal.text)
                                    if m == best || m == fast {
                                        Text(m == best ? (isl ? "Mælt með · nákvæmast" : "Recommended · most accurate")
                                                       : (isl ? "Hraðast" : "Fastest"))
                                            .font(.caption).foregroundStyle(m == best ? pal.accent : pal.text3)
                                    }
                                }
                                Spacer()
                                if on { Image(systemName: "checkmark").font(.subheadline.weight(.semibold)).foregroundStyle(pal.accent)
                                        .transition(.scale.combined(with: .opacity)) }
                            }
                            .padding(.horizontal, 10).padding(.vertical, 9)
                            .background(on ? pal.soft : .clear, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            if changed {
                divider
                Button {
                    model.modelChoice = nil
                } label: {
                    Label(isl ? "Nota ráðlagðar stillingar" : "Reset to recommended", systemImage: "arrow.counterclockwise")
                        .font(.subheadline.weight(.medium)).foregroundStyle(pal.accent)
                        .frame(maxWidth: .infinity).padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .transition(.opacity)
            }
        }
        .padding(.vertical, 6)
        .background(pal.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(pal.text.opacity(0.08)))
        // layered: a soft wide shadow under a tight one
        .shadow(color: .black.opacity(0.10), radius: 2, y: 1)
        .shadow(color: .black.opacity(0.22), radius: 24, y: 12)
        .animation(.spring(duration: 0.35, bounce: 0.2), value: model.modelChoice)
        .animation(.spring(duration: 0.35, bounce: 0.2), value: changed)
        .sensoryFeedback(.selection, trigger: model.modelChoice)
    }

    private var divider: some View { Rectangle().fill(pal.text.opacity(0.07)).frame(height: 1).padding(.horizontal, 14) }

    private func section<C: View>(_ title: String, note: String? = nil, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title.uppercased()).font(.caption2.weight(.semibold)).tracking(0.8).foregroundStyle(pal.text3)
                Spacer()
                if let note { Text(note).font(.caption2).foregroundStyle(pal.text3) }
            }
            .padding(.horizontal, 6)
            content()
        }
        .padding(.horizontal, 10).padding(.vertical, 10)
    }
}

/// A row of options with a sliding highlight; the recommended one carries a small dot.
