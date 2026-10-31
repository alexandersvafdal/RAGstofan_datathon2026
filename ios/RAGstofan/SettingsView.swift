// Server address, answer language, colour palette and light or dark, plus the About page.
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @Environment(\.dismiss) private var dismiss
    @State private var server = ""
    @State private var checking = false

    var body: some View {
        @Bindable var model = model
        let w = Words(lang: model.ui)
        NavigationStack {
            Form {
                Section {
                    TextField("http://10.10.40.10:8000", text: $server)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit(connect)
                    Button(model.ui == .is ? "Tengjast" : "Connect", action: connect).disabled(checking)
                    HStack(spacing: 8) {
                        if checking { ProgressView() }
                        else if let h = model.health {
                            Circle().fill(Color.green).frame(width: 8, height: 8)
                            Text("\(h.model) · \(h.dataSource) · \(fmtNum(Double(h.tablesInCatalog), model.ui))")
                        } else if let e = model.serverError {
                            Circle().fill(Color.red).frame(width: 8, height: 8)
                            Text(e)
                        }
                    }
                    .font(.footnote)
                    .foregroundStyle(pal.text2)
                } header: { Text(w.server) } footer: {
                    Text(model.ui == .is ? "Síminn þarf að vera á sama neti og þjónninn." : "The phone has to be on the same network as the server.")
                }

                Section {
                    Toggle(model.ui == .is ? "Lesa svör upphátt þegar spurt er með rödd" : "Read answers aloud when I ask by voice",
                           isOn: $model.autoSpeak)
                } header: { Text(model.ui == .is ? "Rödd" : "Voice") } footer: {
                    Text(model.ui == .is ? "Talgreining: Whisper. Talgerving: Piper (raddir Steinn og Joe). Allt opinn hugbúnaður á vél liðsins."
                                         : "Speech recognition: Whisper. Speech: Piper (voices Steinn and Joe). All open source, on the team's machine.")
                }

                Section(w.language) {
                    Picker(w.language, selection: $model.langChoice) {
                        ForEach(LangChoice.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }

                Section(w.colour) {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 14) {
                        ForEach(PaletteName.allCases) { name in
                            Swatch(name: name, selected: model.paletteName == name)
                                .onTapGesture { withAnimation(.smooth) { model.paletteName = name } }
                        }
                    }
                    .padding(.vertical, 6)
                    Picker(w.appearance, selection: $model.appearance) {
                        Text("Auto").tag("auto")
                        Text(model.ui == .is ? "Ljóst" : "Light").tag("light")
                        Text(model.ui == .is ? "Dökkt" : "Dark").tag("dark")
                    }
                    .pickerStyle(.segmented)
                }

                Section {
                    NavigationLink(w.about) { AboutView(lang: model.ui) }
                    Button(model.ui == .is ? "Sýna kynninguna aftur" : "Show the introduction again") {
                        dismiss()
                        model.onboarded = false
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(pal.bg)
            .navigationTitle(w.settings)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button(model.ui == .is ? "Lokið" : "Done") { dismiss() } }
            }
            .onAppear { server = model.server }
        }
        .preferredColorScheme(model.colorScheme)
    }

    private func connect() {
        model.server = server
        checking = true
        Task {
            await model.connect()
            checking = false
        }
    }
}

private struct Swatch: View {
    let name: PaletteName
    let selected: Bool
    var body: some View {
        let l = palette(name, dark: false), d = palette(name, dark: true)
        VStack(spacing: 6) {
            ZStack {
                Circle().fill(LinearGradient(colors: [l.bg, d.bg], startPoint: .topLeading, endPoint: .bottomTrailing))
                Circle().fill(l.accent).padding(9)
                Circle().fill(l.series2).frame(width: 10, height: 10).offset(x: 12, y: 12)
            }
            .frame(width: 46, height: 46)
            .overlay(Circle().stroke(selected ? l.accent : .secondary.opacity(0.25), lineWidth: selected ? 3 : 1).padding(-3))
            Text(name.title).font(.caption2).foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
    }
}
