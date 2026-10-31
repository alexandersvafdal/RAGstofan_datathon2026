// The app's state: server, language and theme settings, the conversation, and the dashboard numbers.
import SwiftUI

/// One exchange in the conversation: the question, the steps as they arrive, then the answer or an error.
@MainActor @Observable
final class Exchange: Identifiable {
    let id = UUID()
    let question: String
    let tableId: String?
    let lang: Lang
    var steps: [Step] = []
    var answer: Answer?
    var error: String?
    let started = Date()
    var stepStarted = Date()

    init(question: String, tableId: String?, lang: Lang) {
        self.question = question
        self.tableId = tableId
        self.lang = lang
    }

    var done: Bool { answer != nil || error != nil }
}

@MainActor @Observable
final class AppModel {
    var server: String { didSet { UserDefaults.standard.set(server, forKey: "server") } }
    var langChoice: LangChoice { didSet { UserDefaults.standard.set(langChoice.rawValue, forKey: "lang") } }
    var paletteName: PaletteName { didSet { UserDefaults.standard.set(paletteName.rawValue, forKey: "palette") } }
    var appearance: String { didSet { UserDefaults.standard.set(appearance, forKey: "appearance") } }  // auto | light | dark
    /// The chosen model, or nil for the server's default (its best).
    var modelChoice: String? { didSet { UserDefaults.standard.set(modelChoice, forKey: "model") } }
    var models: ModelList?

    var exchanges: [Exchange] = []
    var indicators: Indicators?
    var health: Health?
    var serverError: String?
    var busy = false

    /// The language for interface text: the chosen one, or English under Auto.
    var ui: Lang { langChoice == .is ? .is : .en }

    init() {
        let d = UserDefaults.standard
        server = d.string(forKey: "server") ?? "http://10.10.40.10:8000"
        langChoice = LangChoice(rawValue: d.string(forKey: "lang") ?? "") ?? .auto
        paletteName = PaletteName(rawValue: d.string(forKey: "palette") ?? "") ?? .warm
        appearance = d.string(forKey: "appearance") ?? "auto"
        modelChoice = d.string(forKey: "model")
    }

    var colorScheme: ColorScheme? { appearance == "light" ? .light : appearance == "dark" ? .dark : nil }

    private var api: API? { try? API(server: server) }

    func connect() async {
        guard let api else { serverError = APIError.badServer(server).localizedDescription; return }
        do {
            health = try await api.health()
            serverError = nil
            models = try? await api.models()
            if let m = models, let c = modelChoice, !m.models.contains(c) { modelChoice = nil }   // gone from the server
            indicators = try await api.indicators()
        } catch {
            health = nil
            serverError = error.localizedDescription
        }
    }

    /// The last few answered turns, the full question each was read as, so follow-ups can chain.
    private var history: [Turn] {
        Array(exchanges.compactMap { e -> Turn? in
            guard let a = e.answer, a.status == "answered" else { return nil }
            let full = a.steps.first { $0.name == "context" }?.detail ?? e.question
            return Turn(question: full, answer: a.text, tables: a.sources.map(\.id))
        }.suffix(3))
    }

    @MainActor
    func ask(_ question: String, tableId: String? = nil) async {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, let api else { return }
        let lang: Lang = langChoice == .auto ? guessLang(q) : (langChoice == .is ? .is : .en)
        let request = AskRequest(question: q, language: langChoice == .auto ? nil : langChoice.rawValue,
                                 tableId: tableId, history: history, model: modelChoice)
        let ex = Exchange(question: q, tableId: tableId, lang: lang)
        withAnimation(.smooth) { exchanges.append(ex) }
        busy = true
        defer { busy = false }
        do {
            for try await event in api.ask(request) {
                switch event {
                case .step(let s):
                    ex.steps.append(s)
                    if s.name != "context" { ex.stepStarted = Date() }  // the next step's clock starts now
                case .answer(let a):
                    withAnimation(.snappy) { ex.answer = a }
                }
            }
        } catch {
            ex.error = error.localizedDescription
        }
    }

    func reset() { exchanges.removeAll() }

    /// The model answering now: the chosen one, or the server's default.
    var activeModel: String? { modelChoice ?? models?.default ?? health?.model }
}
