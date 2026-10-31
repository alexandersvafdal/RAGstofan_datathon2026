// Settings in the app's own style: the connection, how it looks, the language, the voice, and about.
// Cards on the palette's background, one icon per row, the current choice always visible.
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @Environment(\.dismiss) private var dismiss
    @State private var server = ""
    @State private var checking = false
    @FocusState private var editing: Bool

    /// The Spark by name (works anywhere on Tailscale) and by address (the hackathon network).
    private let presets = [("Tailscale", "http://ragstofan-spark.tail4f2a97.ts.net:8000"), ("LAN", "http://10.10.40.10:8000")]

    var body: some View {
        @Bindable var model = model
        let isl = model.ui == .is
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    connection(isl)

                    group(isl ? "Útlit" : "Appearance") {
                        VStack(alignment: .leading, spacing: 16) {
                            HStack(spacing: 10) {
                                ForEach(modes(isl), id: \.0) { v, label in
                                    ModeTile(mode: v, label: label, palette: model.paletteName, on: model.appearance == v) {
                                        withAnimation(.smooth) { model.appearance = v }
                                    }
                                }
                            }
                            divider
                            Text(isl ? "Litir" : "Colours").font(.footnote.weight(.semibold)).foregroundStyle(pal.text2)
                            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 14) {
                                ForEach(PaletteName.allCases) { name in
                                    Swatch(name: name, selected: model.paletteName == name)
                                        .onTapGesture { withAnimation(.smooth) { model.paletteName = name } }
                                }
                            }
                        }
                        .padding(16)
                    }

                    group(isl ? "Tungumál svara" : "Answer language") {
                        Choice(options: LangChoice.allCases.map { ($0, $0.title) }, selection: $model.langChoice)
                            .padding(12)
                    }

                    group(isl ? "Rödd" : "Voice",
                          footer: isl ? "Talgreining með Whisper, tal með Piper og Kokoro. Allt opinn hugbúnaður á vél liðsins."
                                      : "Speech recognition by Whisper, speech by Piper and Kokoro. All open source, on the team's machine.") {
                        VStack(spacing: 0) {
                            VoicePicker().padding(14)
                            divider
                            SettingRow(icon: "speaker.wave.2.fill", tint: .pink,
                                title: isl ? "Lesa svör upphátt" : "Read answers aloud",
                                subtitle: isl ? "Þegar spurt er með rödd" : "When I ask by voice") {
                                Toggle("", isOn: $model.autoSpeak).labelsHidden().tint(pal.accent)
                            }
                        }
                    }

                    group(isl ? "Um" : "About") {
                        VStack(spacing: 0) {
                            NavigationLink { AboutView(lang: model.ui) } label: {
                                SettingRow(icon: "info.circle.fill", tint: .blue, title: isl ? "Um RAGstofan" : "About RAGstofan",
                                    subtitle: isl ? "Hvernig svörin verða til og takmarkanir" : "How answers are made, and the limits") { chevron }
                            }
                            .buttonStyle(.plain)
                            divider
                            Button { dismiss(); model.onboarded = false } label: {
                                SettingRow(icon: "sparkles", tint: .orange, title: isl ? "Sýna kynninguna aftur" : "Show the introduction again") { chevron }
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    VStack(spacing: 4) {
                        Image(systemName: "chart.bar.fill").font(.title3).foregroundStyle(pal.accent)
                        Text("RAGstofan \(version)").font(.footnote.weight(.semibold)).foregroundStyle(pal.text2)
                        Text(isl ? "Lið MOMS · Gagnaþon Hagstofu Íslands 2026" : "Team MOMS · Statistics Iceland Gagnaþon 2026")
                            .font(.caption).foregroundStyle(pal.text3)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 4)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(pal.bg.ignoresSafeArea())
            .navigationTitle(isl ? "Stillingar" : "Settings")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(pal.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark").font(.subheadline.weight(.bold)).foregroundStyle(pal.text2)
                            .frame(width: 30, height: 30).background(pal.text.opacity(0.08), in: Circle())
                    }
                    .accessibilityLabel(isl ? "Loka" : "Close")
                }
            }
            .onAppear { server = model.server }
        }
        .preferredColorScheme(model.colorScheme)
        .tint(pal.accent)
    }

    private func modes(_ isl: Bool) -> [(String, String)] {
        [("auto", "Auto"), ("light", isl ? "Ljóst" : "Light"), ("dark", isl ? "Dökkt" : "Dark")]
    }
    private var version: String {
        let info = Bundle.main.infoDictionary
        let v = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let b = info?["CFBundleVersion"] as? String ?? ""
        return b.isEmpty ? v : "\(v) (\(b))"
    }

    // MARK: connection

    private func connection(_ isl: Bool) -> some View {
        group(isl ? "Tenging" : "Connection",
              footer: isl ? "Tailscale virkar hvar sem er; LAN aðeins á neti Gagnaþonsins." : "Tailscale works anywhere; LAN only on the hackathon network.") {
            VStack(alignment: .leading, spacing: 14) {
                // status: what the app is talking to right now
                HStack(spacing: 12) {
                    ZStack {
                        Circle().fill(statusColor.opacity(0.16)).frame(width: 40, height: 40)
                        if checking { ProgressView().tint(statusColor) }
                        else { Image(systemName: statusIcon).font(.headline).foregroundStyle(statusColor).contentTransition(.symbolEffect(.replace)) }
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(statusTitle(isl)).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text)
                        Text(statusDetail(isl)).font(.caption).foregroundStyle(pal.text3).lineLimit(2)
                    }
                    Spacer(minLength: 0)
                }
                .animation(.smooth, value: checking)

                // the address, with its own Connect button
                HStack(spacing: 8) {
                    Image(systemName: "server.rack").font(.subheadline).foregroundStyle(pal.text3)
                    TextField("http://…:8000", text: $server)
                        .font(.callout.monospaced())
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .focused($editing).submitLabel(.go).onSubmit(connect)
                        .foregroundStyle(pal.text)
                    Button(action: connect) {
                        Text(isl ? "Tengjast" : "Connect").font(.footnote.weight(.semibold))
                            .foregroundStyle(pal.accentInk)
                            .padding(.horizontal, 12).padding(.vertical, 7)
                            .background(pal.accent, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .disabled(checking || server.isEmpty)
                    .opacity(checking || server.isEmpty ? 0.5 : 1)
                }
                .padding(.leading, 12).padding(.trailing, 6).padding(.vertical, 6)
                .background(pal.bg, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(editing ? pal.accent.opacity(0.6) : pal.text.opacity(0.08), lineWidth: editing ? 1.5 : 1))

                // one tap to the Spark by name or by address
                HStack(spacing: 8) {
                    ForEach(presets, id: \.0) { name, url in
                        let on = model.server == url
                        Button { server = url; connect() } label: {
                            Label(name, systemImage: on ? "checkmark.circle.fill" : (name == "LAN" ? "wifi" : "network"))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(on ? pal.accent : pal.text2)
                                .padding(.horizontal, 11).padding(.vertical, 7)
                                .background(on ? pal.soft : pal.text.opacity(0.05), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(16)
        }
    }

    private var statusColor: Color {
        switch model.connection {
        case .connected: .green
        case .connecting: .orange
        case .offline: .red
        }
    }
    private var statusIcon: String {
        switch model.connection {
        case .connected: "checkmark"
        case .connecting: "antenna.radiowaves.left.and.right"
        case .offline: "wifi.exclamationmark"
        }
    }
    private func statusTitle(_ isl: Bool) -> String {
        if checking { return isl ? "Tengist…" : "Connecting…" }
        switch model.connection {
        case .connected: return isl ? "Tengt" : "Connected"
        case .connecting: return isl ? "Tengist…" : "Connecting…"
        case .offline: return isl ? "Næ ekki í þjóninn" : "Can't reach the server"
        }
    }
    private func statusDetail(_ isl: Bool) -> String {
        if let h = model.health, case .connected = model.connection {
            return "\(prettyModel(h.model)) · \(fmtNum(Double(h.tablesInCatalog), model.ui)) \(isl ? "töflur" : "tables") · \(h.dataSource == "local" ? (isl ? "staðbundin gögn" : "local data") : "PX-API")"
        }
        if case .offline(let e) = model.connection { return e }
        return model.server
    }

    private func connect() {
        editing = false
        model.server = server
        checking = true
        Task {
            await model.connect()
            checking = false
        }
    }

    // MARK: building blocks

    private var divider: some View { Rectangle().fill(pal.text.opacity(0.07)).frame(height: 1).padding(.leading, 14) }
    private var chevron: some View { Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(pal.text3) }

    /// A titled card; an optional quiet line underneath.
    private func group<C: View>(_ title: String, footer: String? = nil, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.caption.weight(.semibold)).tracking(0.8).foregroundStyle(pal.text3).padding(.leading, 6)
            content()
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(pal.card, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(pal.text.opacity(0.06)))
            if let footer { Text(footer).font(.caption).foregroundStyle(pal.text3).padding(.horizontal, 6) }
        }
    }
}

/// A settings row: a tinted icon tile, a title with an optional subtitle, and whatever goes on the right.
private struct SettingRow<Trailing: View>: View {
    @Environment(\.pal) private var pal
    let icon: String
    let tint: Color
    let title: String
    var subtitle: String? = nil
    @ViewBuilder let trailing: Trailing
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 30, height: 30).background(tint.gradient, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline).foregroundStyle(pal.text)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(pal.text3) }
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .contentShape(Rectangle())
    }
}

/// Options side by side with a sliding highlight.
private struct Choice<V: Hashable>: View {
    @Environment(\.pal) private var pal
    let options: [(V, String)]
    @Binding var selection: V
    @Namespace private var ns
    var body: some View {
        HStack(spacing: 2) {
            ForEach(options, id: \.0) { v, label in
                let on = v == selection
                Button { withAnimation(.spring(duration: 0.35, bounce: 0.25)) { selection = v } } label: {
                    Text(label).font(.subheadline.weight(on ? .semibold : .regular)).foregroundStyle(on ? pal.text : pal.text2)
                        .frame(maxWidth: .infinity).padding(.vertical, 9)
                        .background {
                            if on {
                                RoundedRectangle(cornerRadius: 10, style: .continuous).fill(pal.bg)
                                    .shadow(color: .black.opacity(0.12), radius: 3, y: 1)
                                    .matchedGeometryEffect(id: "on", in: ns)
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(pal.text.opacity(0.06), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .sensoryFeedback(.selection, trigger: selection)
    }
}

/// Auto, Light or Dark as a little screen in that look: a bar, a bubble and a card.
private struct ModeTile: View {
    @Environment(\.pal) private var pal
    let mode: String
    let label: String
    let palette: PaletteName
    let on: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                ZStack {
                    if mode == "auto" {
                        HStack(spacing: 0) { screen(dark: false); screen(dark: true) }
                    } else {
                        screen(dark: mode == "dark")
                    }
                }
                .frame(height: 74)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(on ? pal.accent : pal.text.opacity(0.12), lineWidth: on ? 2.5 : 1))
                HStack(spacing: 4) {
                    if on { Image(systemName: "checkmark.circle.fill").foregroundStyle(pal.accent).transition(.scale) }
                    Text(label).foregroundStyle(on ? pal.text : pal.text2)
                }
                .font(.caption.weight(.semibold))
            }
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .sensoryFeedback(.selection, trigger: on)
    }

    private func screen(dark: Bool) -> some View {
        let p = RAGstofan.palette(palette, dark: dark)
        return ZStack(alignment: .topLeading) {
            p.bg
            VStack(alignment: .leading, spacing: 5) {
                Capsule().fill(p.text.opacity(0.25)).frame(width: 26, height: 4)
                HStack { Spacer(); Capsule().fill(p.accent).frame(width: 30, height: 9) }
                RoundedRectangle(cornerRadius: 4).fill(p.card).frame(height: 22)
                    .overlay(alignment: .leading) { Capsule().fill(p.series1).frame(width: 22, height: 4).padding(.leading, 5) }
            }
            .padding(8)
        }
    }
}

private struct Swatch: View {
    @Environment(\.pal) private var pal
    let name: PaletteName
    let selected: Bool
    var body: some View {
        let l = palette(name, dark: false), d = palette(name, dark: true)
        VStack(spacing: 6) {
            ZStack {
                Circle().fill(LinearGradient(colors: [l.bg, d.bg], startPoint: .topLeading, endPoint: .bottomTrailing))
                Circle().fill(l.accent).padding(9)
                Circle().fill(l.series2).frame(width: 10, height: 10).offset(x: 12, y: 12)
                if selected {
                    Image(systemName: "checkmark").font(.caption.weight(.heavy)).foregroundStyle(l.accentInk).transition(.scale)
                }
            }
            .frame(width: 46, height: 46)
            .overlay(Circle().stroke(selected ? l.accent : pal.text.opacity(0.12), lineWidth: selected ? 2.5 : 1).padding(-3))
            .scaleEffect(selected ? 1.06 : 1)
            Text(name.title).font(.caption2.weight(selected ? .semibold : .regular)).foregroundStyle(selected ? pal.text : pal.text3)
        }
        .contentShape(Rectangle())
        .animation(.spring(duration: 0.35, bounce: 0.3), value: selected)
    }
}
