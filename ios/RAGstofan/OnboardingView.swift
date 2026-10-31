// The first-run introduction: what RAGstofan is, how it answers, a check that the server answers, then the look
// (colour and light or dark) and the language. Every choice applies at once, so the screens restyle as you pick.
import SwiftUI

struct OnboardingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @State private var page = 0
    private let pages = 6

    private var isl: Bool { model.ui == .is }
    private func t(_ en: String, _ is_: String) -> String { isl ? is_ : en }

    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $page.animation(.spring(duration: 0.5, bounce: 0.15))) {
                Welcome(t: t).tag(0)
                HowItWorks(t: t).tag(1)
                ConnectionPage(t: t).tag(2)
                Look(t: t).tag(3)
                Language(t: t).tag(4)
                VoicePage(t: t).tag(5)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))

            // dots, then back and next
            HStack(spacing: 7) {
                ForEach(0..<pages, id: \.self) { i in
                    Capsule().fill(i == page ? pal.accent : pal.text.opacity(0.18))
                        .frame(width: i == page ? 22 : 7, height: 7)
                }
            }
            .animation(.spring(duration: 0.4, bounce: 0.3), value: page)
            .padding(.bottom, 18)

            HStack(spacing: 12) {
                if page > 0 {
                    Button { withAnimation(.spring(duration: 0.5, bounce: 0.15)) { page -= 1 } } label: {
                        Image(systemName: "chevron.left").font(.headline)
                            .frame(width: 54, height: 54)
                            .foregroundStyle(pal.text2)
                            .background(pal.card, in: Circle())
                            .overlay(Circle().stroke(pal.text.opacity(0.1)))
                    }
                    .transition(.scale.combined(with: .opacity))
                }
                Button {
                    if page < pages - 1 { withAnimation(.spring(duration: 0.5, bounce: 0.15)) { page += 1 } }
                    else { model.onboarded = true }
                } label: {
                    Text(page < pages - 1 ? t("Continue", "Áfram") : t("Get started", "Byrja"))
                        .font(.headline)
                        .contentTransition(.interpolate)
                        .frame(maxWidth: .infinity)
                        .frame(height: 54)
                        .foregroundStyle(pal.accentInk)
                        .background(pal.accent, in: Capsule())
                }
            }
            .animation(.spring(duration: 0.4, bounce: 0.2), value: page)
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
            .sensoryFeedback(.selection, trigger: page)
        }
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity)
        .background(pal.bg.ignoresSafeArea())
        .preferredColorScheme(model.colorScheme)
        .animation(.smooth, value: model.paletteName)
    }
}

/// A page: a picture, a title, a line of text, then the page's own controls.
private struct Page<Hero: View, Content: View>: View {
    @Environment(\.pal) private var pal
    let title: String
    let text: String
    @ViewBuilder let hero: Hero
    @ViewBuilder let content: Content
    @State private var shown = false

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                hero.reveal(0, shown)
                VStack(spacing: 10) {
                    Text(title).font(.system(size: 30, weight: .bold, design: .rounded)).foregroundStyle(pal.text)
                        .multilineTextAlignment(.center)
                    Text(text).font(.callout).foregroundStyle(pal.text2).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .reveal(1, shown)
                content.reveal(2, shown)
            }
            .padding(.horizontal, 28)
            .padding(.top, 44)
            .padding(.bottom, 20)
        }
        .scrollBounceBehavior(.basedOnSize)
        .onAppear { shown = true }
    }
}

/// The logo's three bars rising one after another.
private struct RisingBars: View {
    @Environment(\.pal) private var pal
    @State private var up = false
    var body: some View {
        HStack(alignment: .bottom, spacing: 10) {
            ForEach(0..<3, id: \.self) { i in
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(LinearGradient(colors: [pal.accent2, pal.accent], startPoint: .top, endPoint: .bottom))
                    .frame(width: 30, height: up ? [54, 84, 120][i] : 12)
                    .animation(.spring(duration: 0.8, bounce: 0.35).delay(0.15 + Double(i) * 0.12), value: up)
            }
        }
        .frame(height: 130, alignment: .bottom)
        .onAppear { up = true }
    }
}

private struct Welcome: View {
    @Environment(\.pal) private var pal
    let t: (String, String) -> String
    var body: some View {
        Page(title: t("Iceland, in numbers.", "Ísland í tölum."),
             text: t("Ask about Statistics Iceland's tables in plain English or Icelandic. Every answer gives the figure, its unit and the table it came from.",
                     "Spurðu um töflur Hagstofu Íslands á íslensku eða ensku. Hverju svari fylgir talan, eining hennar og taflan sem hún kemur úr.")) {
            RisingBars()
        } content: {
            VStack(spacing: 26) {
                VStack(alignment: .leading, spacing: 14) {
                    Feature(icon: "text.magnifyingglass", text: t("Finds the right table among about 1,900", "Finnur réttu töfluna af um 1.900"))
                    Feature(icon: "checkmark.seal", text: t("Checks every number against the table", "Ber hverja tölu saman við töfluna"))
                    Feature(icon: "chart.xyaxis.line", text: t("Draws the trend when there is one", "Teiknar þróunina þegar hún er til"))
                }
            }
        }
    }
}

private struct Feature: View {
    @Environment(\.pal) private var pal
    let icon: String
    let text: String
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon).font(.title3).foregroundStyle(pal.accent).frame(width: 30)
            Text(text).font(.subheadline).foregroundStyle(pal.text)
            Spacer(minLength: 0)
        }
    }
}

private struct HowItWorks: View {
    @Environment(\.pal) private var pal
    let t: (String, String) -> String
    var body: some View {
        let steps = [("text.bubble", t("Understand", "Skilja"), t("reads the question, splits two-part ones", "les spurninguna, skiptir tvíþættum")),
                     ("tablecells", t("Find the table", "Finna töfluna"), t("keywords, meaning search, then the model", "leitarorð, merkingarleit, svo líkanið")),
                     ("checklist", t("Choose categories", "Velja flokka"), t("only values the table really has", "aðeins gildi sem taflan hefur")),
                     ("function", t("Read and calculate", "Lesa og reikna"), t("arithmetic in code, never by the model", "reikningur í kóða, aldrei af líkaninu")),
                     ("pencil.line", t("Write", "Skrifa"), t("a short answer, numbers checked", "stutt svar, tölur yfirfarnar"))]
        Page(title: t("How it answers", "Hvernig það svarar"),
             text: t("You watch each step as it happens. If it takes too long, “Answer now” gives a quick answer from a faster model.",
                     "Þú sérð hvert skref gerast. Ef það tekur of langan tíma gefur „Svara strax“ fljótt svar frá hraðara líkani.")) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 56, weight: .light)).foregroundStyle(pal.accent)
                .symbolEffect(.pulse)
        } content: {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(steps.enumerated()), id: \.offset) { i, s in
                    HStack(alignment: .top, spacing: 14) {
                        VStack(spacing: 0) {
                            Image(systemName: s.0).font(.subheadline.weight(.semibold)).foregroundStyle(pal.accentInk)
                                .frame(width: 34, height: 34).background(pal.accent, in: Circle())
                            if i < steps.count - 1 { Rectangle().fill(pal.accent.opacity(0.25)).frame(width: 2, height: 18) }
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.1).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text)
                            Text(s.2).font(.caption).foregroundStyle(pal.text2)
                        }
                        .padding(.top, 7)
                        Spacer(minLength: 0)
                    }
                }
            }
            .padding(18)
            .background(pal.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        }
    }
}

/// Check that the server answers before going on; the address can be changed here.
private struct ConnectionPage: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let t: (String, String) -> String
    @State private var server = ""
    @State private var checking = false

    var body: some View {
        Page(title: t("Connect to the server", "Tengjast þjóninum"),
             text: t("RAGstofan runs on the team's machine. The phone has to be on the same network.",
                     "RAGstofan keyrir á vél liðsins. Síminn þarf að vera á sama neti.")) {
                ZStack {
                    Circle().fill(statusColor.opacity(0.14)).frame(width: 120, height: 120)
                    Circle().stroke(statusColor.opacity(0.35), lineWidth: 2).frame(width: 120, height: 120)
                        .scaleEffect(checking ? 1.25 : 1).opacity(checking ? 0 : 1)
                        .animation(checking ? .easeOut(duration: 1).repeatForever(autoreverses: false) : .default, value: checking)
                    Image(systemName: checking ? "antenna.radiowaves.left.and.right" : model.health != nil ? "checkmark" : "wifi.exclamationmark")
                        .font(.system(size: 40, weight: .semibold))
                        .foregroundStyle(statusColor)
                        .contentTransition(.symbolEffect(.replace))
                        .symbolEffect(.variableColor.iterative, isActive: checking)
                }
                .animation(.spring(duration: 0.4), value: checking)
        } content: {
                VStack(spacing: 10) {
                    TextField("http://10.10.40.10:8000", text: $server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit(check)
                        .padding(.horizontal, 16).frame(height: 48)
                        .background(pal.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(pal.text.opacity(0.1)))
                    Button(action: check) {
                        Label(t("Check connection", "Athuga tengingu"), systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(PillStyle())
                    .disabled(checking)
                    Group {
                        if checking { Text(t("Checking…", "Athuga…")) }
                        else if let h = model.health {
                            Text(t("Connected · \(h.model) · \(fmtNum(Double(h.tablesInCatalog), .en)) tables",
                                   "Tengt · \(h.model) · \(fmtNum(Double(h.tablesInCatalog), .is)) töflur"))
                        } else if let e = model.serverError { Text(e) }
                    }
                    .font(.footnote).foregroundStyle(pal.text2).multilineTextAlignment(.center)
                    .transition(.opacity)
                }
                .sensoryFeedback(.success, trigger: model.health?.model)
        }
        .onAppear {
            server = model.server
            check()
        }
    }

    private var statusColor: Color { checking ? pal.accent : model.health != nil ? .green : .orange }

    private func check() {
        model.server = server
        checking = true
        Task {
            await model.connect()
            checking = false
        }
    }
}

/// Colour and light or dark; the whole introduction restyles as you pick.
private struct Look: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let t: (String, String) -> String

    var body: some View {
        @Bindable var model = model
        Page(title: t("Make it yours", "Gerðu það að þínu"),
             text: t("Pick a colour and light or dark. You can change both later in Settings.",
                     "Veldu lit og ljóst eða dökkt. Þú getur breytt hvoru tveggja síðar í Stillingum.")) {
            Preview()
        } content: {
            VStack(spacing: 20) {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 14) {
                    ForEach(PaletteName.allCases) { name in
                        let p = palette(name, dark: false)
                        VStack(spacing: 6) {
                            ZStack {
                                Circle().fill(p.bg)
                                Circle().fill(p.accent).padding(8)
                                Circle().fill(p.series2).frame(width: 10, height: 10).offset(x: 11, y: 11)
                            }
                            .frame(width: 46, height: 46)
                            .overlay(Circle().stroke(model.paletteName == name ? pal.accent : pal.text.opacity(0.12),
                                                     lineWidth: model.paletteName == name ? 3 : 1).padding(-3))
                            .scaleEffect(model.paletteName == name ? 1.08 : 1)
                            Text(name.title).font(.caption2).foregroundStyle(pal.text2)
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { withAnimation(.spring(duration: 0.45, bounce: 0.3)) { model.paletteName = name } }
                    }
                }
                Picker(t("Appearance", "Útlit"), selection: $model.appearance.animation(.smooth)) {
                    Text("Auto").tag("auto")
                    Text(t("Light", "Ljóst")).tag("light")
                    Text(t("Dark", "Dökkt")).tag("dark")
                }
                .pickerStyle(.segmented)
            }
            .sensoryFeedback(.selection, trigger: model.paletteName)
        }
    }
}

/// A small answer card in the chosen colours.
private struct Preview: View {
    @Environment(\.pal) private var pal
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Spacer()
                Text("Hve margir búa á Íslandi?").font(.caption).foregroundStyle(pal.accentInk)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(pal.accent, in: Capsule())
            }
            HStack(alignment: .bottom) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("394.324").font(.system(size: 26, weight: .semibold, design: .rounded)).foregroundStyle(pal.text)
                    Text("MAN00101 · 2026").font(.caption2).foregroundStyle(pal.text3)
                }
                Spacer()
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach([0.45, 0.6, 0.7, 0.85, 1.0], id: \.self) { h in
                        RoundedRectangle(cornerRadius: 3).fill(pal.series1.opacity(0.4 + h * 0.6)).frame(width: 10, height: 44 * h)
                    }
                }
            }
            .padding(14)
            .background(pal.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .padding(14)
        .background(pal.bg, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(pal.text.opacity(0.08)))
        .shadow(color: .black.opacity(0.08), radius: 16, y: 8)
    }
}

private struct Language: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let t: (String, String) -> String

    var body: some View {
        let options: [(LangChoice, String, String, String)] = [
            (.auto, "globe", t("Automatic", "Sjálfvirkt"), t("Answers in the language you ask in", "Svarar á málinu sem spurt er á")),
            (.is, "character.bubble", "Íslenska", t("Answers and interface in Icelandic", "Svör og viðmót á íslensku")),
            (.en, "textformat", "English", t("Answers and interface in English", "Svör og viðmót á ensku"))]
        Page(title: t("Language", "Tungumál"),
             text: t("Ask in either language. Choose how RAGstofan answers.", "Spurðu á hvoru málinu sem er. Veldu hvernig RAGstofan svarar.")) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 56, weight: .light)).foregroundStyle(pal.accent)
                .symbolEffect(.bounce, value: model.langChoice)
        } content: {
            VStack(spacing: 10) {
                ForEach(options, id: \.0) { o in
                    let on = model.langChoice == o.0
                    Button { withAnimation(.spring(duration: 0.4, bounce: 0.25)) { model.langChoice = o.0 } } label: {
                        HStack(spacing: 14) {
                            Image(systemName: o.1).font(.title3).frame(width: 30).foregroundStyle(on ? pal.accentInk : pal.accent)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(o.2).font(.headline)
                                Text(o.3).font(.caption).opacity(0.8)
                            }
                            .foregroundStyle(on ? pal.accentInk : pal.text)
                            Spacer()
                            Image(systemName: on ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(on ? pal.accentInk : pal.text3)
                                .contentTransition(.symbolEffect(.replace))
                        }
                        .padding(16)
                        .background(on ? pal.accent : pal.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(pal.text.opacity(on ? 0 : 0.1)))
                    }
                    .buttonStyle(.plain)
                }
            }
            .sensoryFeedback(.selection, trigger: model.langChoice)
        }
    }
}

/// Pick the voices answers are read in, hearing each first.
private struct VoicePage: View {
    @Environment(\.pal) private var pal
    let t: (String, String) -> String
    var body: some View {
        Page(title: t("Pick a voice", "Veldu rödd"),
             text: t("Ask with the microphone and hear the answer. Tap a voice to hear it.",
                     "Spurðu með hljóðnemanum og heyrðu svarið. Ýttu á rödd til að heyra hana.")) {
            Image(systemName: "waveform.and.mic")
                .font(.system(size: 56, weight: .light)).foregroundStyle(pal.accent)
                .symbolEffect(.variableColor.iterative.reversing)
        } content: {
            VoicePicker()
        }
    }
}
