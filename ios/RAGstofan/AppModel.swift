// The app's state: server, language and theme settings, how questions are answered, the conversation, saved past
// conversations, and the dashboard numbers.
import SwiftUI

/// One exchange in the conversation: the question, the steps as they arrive, then the answer or an error.
@MainActor @Observable
final class Exchange: Identifiable {
    let id = UUID()
    let question: String
    let tableId: String?
    let lang: Lang
    let fast: Bool
    var spoken = false   // asked by voice, so the answer is read aloud
    var steps: [Step] = []
    var answer: Answer?
    var error: String?
    let started = Date()
    var stepStarted = Date()

    init(question: String, tableId: String?, lang: Lang, fast: Bool = false, answer: Answer? = nil) {
        self.question = question
        self.tableId = tableId
        self.lang = lang
        self.fast = fast
        self.answer = answer
    }

    var done: Bool { answer != nil || error != nil }
}

/// A past conversation, kept on this device only.
struct Conversation: Codable, Identifiable {
    struct Saved: Codable { let question: String; let tableId: String?; let lang: String; let fast: Bool; let answer: Answer }
    let id: UUID
    let started: Date
    var updated: Date
    var exchanges: [Saved]
    var title: String { exchanges.first?.question ?? "" }
}

@MainActor @Observable
final class AppModel {
    var server: String { didSet { UserDefaults.standard.set(server, forKey: "server") } }
    var langChoice: LangChoice { didSet { UserDefaults.standard.set(langChoice.rawValue, forKey: "lang") } }
    var paletteName: PaletteName { didSet { UserDefaults.standard.set(paletteName.rawValue, forKey: "palette") } }
    var appearance: String { didSet { UserDefaults.standard.set(appearance, forKey: "appearance") } }  // auto | light | dark
    /// How questions are answered. nil everywhere means the server's defaults: the best model, the longest thinking.
    var modelChoice: String? { didSet { UserDefaults.standard.set(modelChoice, forKey: "model") } }
    var reasoningChoice: String? { didSet { UserDefaults.standard.set(reasoningChoice, forKey: "reasoning") } }
    var temperatureChoice: Double? { didSet { UserDefaults.standard.set(temperatureChoice, forKey: "temperature") } }
    var models: ModelList?
    /// The first-run introduction has been seen.
    var onboarded: Bool { didSet { UserDefaults.standard.set(onboarded, forKey: "onboarded") } }
    /// Read the answer aloud when the question was asked by voice.
    var autoSpeak: Bool { didSet { UserDefaults.standard.set(autoSpeak, forKey: "autoSpeak") } }
    let voice = Voice()
    /// The chosen voice per language ("en", "is"); missing means the server's default.
    var voiceChoice: [String: String] { didSet { UserDefaults.standard.set(voiceChoice, forKey: "voices") } }
    var voiceList: VoiceList?

    var exchanges: [Exchange] = []
    var conversations: [Conversation] = []
    private var currentId: UUID?   // the saved conversation the screen shows, once it has an answer
    var indicators: Indicators?
    var health: Health?
    var serverError: String?
    var busy = false
    private var running: Task<Void, Never>?

    /// The language for interface text: the chosen one, or English under Auto.
    var ui: Lang { langChoice == .is ? .is : .en }

    init() {
        let d = UserDefaults.standard
        server = d.string(forKey: "server") ?? "http://10.10.40.10:8000"
        langChoice = LangChoice(rawValue: d.string(forKey: "lang") ?? "") ?? .auto
        paletteName = PaletteName(rawValue: d.string(forKey: "palette") ?? "") ?? .warm
        appearance = d.string(forKey: "appearance") ?? "auto"
        modelChoice = d.string(forKey: "model")
        reasoningChoice = d.string(forKey: "reasoning")
        temperatureChoice = d.object(forKey: "temperature") as? Double
        conversations = Self.load()
        onboarded = d.bool(forKey: "onboarded")
        autoSpeak = d.object(forKey: "autoSpeak") as? Bool ?? true
        voiceChoice = d.dictionary(forKey: "voices") as? [String: String] ?? [:]
    }

    var colorScheme: ColorScheme? { appearance == "light" ? .light : appearance == "dark" ? .dark : nil }

    private var api: API? { try? API(server: server) }
    var apiClient: API? { api }

    func connect() async {
        guard let api else { serverError = APIError.badServer(server).localizedDescription; return }
        do {
            health = try await api.health()
            serverError = nil
            models = try? await api.models()
            voiceList = try? await api.voices()
            if let m = models, let c = modelChoice, !m.models.contains(c) { modelChoice = nil }   // gone from the server
            indicators = try await api.indicators()
        } catch {
            health = nil
            serverError = error.localizedDescription
        }
    }

    /// The model answering now: the chosen one, or the server's default.
    var activeModel: String? { modelChoice ?? models?.default ?? health?.model }
    var activeReasoning: String { reasoningChoice ?? models?.reasoningDefault ?? "high" }

    /// The last few answered turns, the full question each was read as, so follow-ups can chain.
    private var history: [Turn] {
        Array(exchanges.compactMap { e -> Turn? in
            guard let a = e.answer, a.status == "answered" else { return nil }
            let full = a.steps.first { $0.name == "context" }?.detail ?? e.question
            return Turn(question: full, answer: a.text, tables: a.sources.map(\.id))
        }.suffix(3))
    }

    /// Ask, keeping hold of the request so "Answer now" can stop it.
    func send(_ question: String, tableId: String? = nil, fast: Bool = false, spoken: Bool = false) {
        running = Task { await ask(question, tableId: tableId, fast: fast, spoken: spoken) }
    }

    /// Read an answer aloud in its own language, or stop reading it.
    func speak(_ ex: Exchange) {
        guard let a = ex.answer else { return }
        let lang: Lang = a.language == "is" ? .is : .en
        Task { await voice.toggleSpeaking(ex.id.uuidString, text: prettyPeriods(a.text, lang), language: a.language,
                                          voice: voiceChoice[a.language], api: api) }
    }

    /// Stop the question in progress (the server stops with it) and ask again with the fast model, thinking off.
    func answerNow(_ ex: Exchange) {
        running?.cancel()
        exchanges.removeAll { $0.id == ex.id }
        busy = false
        send(ex.question, tableId: ex.tableId, fast: true, spoken: ex.spoken)
    }

    private func ask(_ question: String, tableId: String?, fast: Bool, spoken: Bool) async {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, let api else { return }
        let lang: Lang = langChoice == .auto ? guessLang(q) : (langChoice == .is ? .is : .en)
        let request = AskRequest(question: q, language: langChoice == .auto ? nil : langChoice.rawValue,
                                 tableId: tableId, history: history, model: modelChoice,
                                 temperature: temperatureChoice, reasoning: reasoningChoice, fast: fast)
        let ex = Exchange(question: q, tableId: tableId, lang: lang, fast: fast)
        ex.spoken = spoken
        withAnimation(.smooth) { exchanges.append(ex) }
        busy = true
        do {
            for try await event in api.ask(request) {
                switch event {
                case .step(let s):
                    ex.steps.append(s)
                    if s.name != "context" { ex.stepStarted = Date() }  // the next step's clock starts now
                case .answer(let a):
                    withAnimation(.snappy) { ex.answer = a }
                    save()
                    if spoken && autoSpeak && a.status != "error" { speak(ex) }
                }
            }
        } catch {
            if Task.isCancelled { return }   // replaced by the quick answer
            ex.error = error.localizedDescription
        }
        busy = false
    }

    func reset() {
        voice.stopSpeaking()
        running?.cancel()
        busy = false
        exchanges.removeAll()
        currentId = nil
    }

    // MARK: past conversations, saved as JSON in Application Support on this device

    private static var file: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("conversations.json")
    }

    private static func load() -> [Conversation] {
        guard let data = try? Data(contentsOf: file) else { return [] }
        return (try? JSONDecoder().decode([Conversation].self, from: data)) ?? []
    }

    private func write() {
        if let data = try? JSONEncoder().encode(conversations) { try? data.write(to: Self.file, options: .atomic) }
    }

    /// Store the conversation on screen, newest first, after each answer.
    private func save() {
        let saved = exchanges.compactMap { e in
            e.answer.map { Conversation.Saved(question: e.question, tableId: e.tableId, lang: e.lang.rawValue, fast: e.fast, answer: $0) }
        }
        guard !saved.isEmpty else { return }
        let id = currentId ?? UUID()
        currentId = id
        let started = conversations.first { $0.id == id }?.started ?? Date()
        conversations.removeAll { $0.id == id }
        conversations.insert(Conversation(id: id, started: started, updated: Date(), exchanges: saved), at: 0)
        write()
    }

    /// Show a past conversation; asking more continues it.
    func open(_ c: Conversation) {
        reset()
        exchanges = c.exchanges.map { Exchange(question: $0.question, tableId: $0.tableId, lang: Lang(rawValue: $0.lang) ?? .en,
                                               fast: $0.fast, answer: $0.answer) }
        currentId = c.id
    }

    /// Pick a voice and hear a short example in it.
    func tryVoice(_ v: VoiceOption, language: String) {
        voiceChoice[language] = v.id == voiceList?.default[language] ? nil : v.id
        let sample = language == "is" ? "Íbúar Íslands voru 394.324 í byrjun árs 2026."
                                      : "Iceland had 394,324 inhabitants at the start of 2026."
        Task { await voice.toggleSpeaking("sample-\(v.id)", text: sample, language: language, voice: v.id, api: api) }
    }

    func activeVoice(_ language: String) -> String? { voiceChoice[language] ?? voiceList?.default[language] }

    func delete(_ ids: [UUID]) {
        conversations.removeAll { ids.contains($0.id) }
        if let c = currentId, ids.contains(c) { currentId = nil }
        write()
    }
}
