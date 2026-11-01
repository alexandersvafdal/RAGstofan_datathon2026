// The app's state: server, language and theme settings, how questions are answered, the conversation, saved past
// conversations, and the dashboard numbers.
import SwiftUI
import ActivityKit
import UIKit
import UserNotifications

/// Whether the server answers: shown on the home page, retried on its own while it doesn't.
enum Connection: Equatable { case connecting, connected, offline(String) }

/// One exchange in the conversation: the question, the steps as they arrive, then the answer or an error.
@MainActor @Observable
final class Exchange: Identifiable {
    let id = UUID()
    let question: String
    let tableId: String?
    let lang: Lang
    let fast: Bool
    var spoken = false   // asked by voice, so the answer is read aloud
    var suggestions: [Suggestion]?   // follow-ups, arriving after the answer; nil while they are being written
    var suggestionsFailed = false
    var steps: [Step] = []
    var answer: Answer?
    var error: String?
    let requestId = UUID().uuidString   // the server keeps the answer under this if the phone drops the line
    var reconnecting = false            // the stream was cut (the app slept); collecting the answer from the server
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
    /// Private mode: nothing from this conversation is saved to history while it is on.
    var privateMode: Bool { didSet { UserDefaults.standard.set(privateMode, forKey: "private") } }
    var voiceList: VoiceList?

    var exchanges: [Exchange] = []
    var conversations: [Conversation] = []
    private var currentId: UUID?   // the saved conversation the screen shows, once it has an answer
    var indicators: Indicators?
    var health: Health?
    var serverError: String?
    var connection: Connection = .connecting
    var nextRetry: Date?                                   // when an unreachable server is tried again
    private var activities: [String: Activity<QuestionActivity>] = [:]   // each question's Live Activity
    var busy = false
    private var running: Task<Void, Never>?

    /// The language for interface text: the chosen one, or English under Auto.
    var ui: Lang { langChoice == .is ? .is : .en }

    init() {
        let d = UserDefaults.standard
        server = d.string(forKey: "server") ?? "http://10.10.40.10:8000"
        langChoice = LangChoice(rawValue: d.string(forKey: "lang") ?? "") ?? .auto
        // Sterling became the default on 1 Nov 2026: every install moves to it once, then keeps what the user picks
        if !d.bool(forKey: "sterlingDefault") { d.set(PaletteName.sterling.rawValue, forKey: "palette"); d.set(true, forKey: "sterlingDefault") }
        paletteName = PaletteName(rawValue: d.string(forKey: "palette") ?? "") ?? .sterling
        appearance = d.string(forKey: "appearance") ?? "auto"
        modelChoice = d.string(forKey: "model")
        reasoningChoice = d.string(forKey: "reasoning")
        temperatureChoice = d.object(forKey: "temperature") as? Double
        conversations = Self.load()
        onboarded = d.bool(forKey: "onboarded")
        autoSpeak = d.object(forKey: "autoSpeak") as? Bool ?? true
        privateMode = d.bool(forKey: "private")
        voiceChoice = d.dictionary(forKey: "voices") as? [String: String] ?? [:]
    }

    var colorScheme: ColorScheme? { appearance == "light" ? .light : appearance == "dark" ? .dark : nil }

    private var api: API? { try? API(server: server) }
    var apiClient: API? { api }

    func connect() async {
        guard let api else {
            serverError = APIError.badServer(server).localizedDescription
            withAnimation(.spring(duration: 0.5)) { connection = .offline(serverError ?? "") }
            return
        }
        if connection != .connected { withAnimation(.spring(duration: 0.4)) { connection = .connecting } }
        do {
            health = try await api.health()
            serverError = nil
            nextRetry = nil
            withAnimation(.spring(duration: 0.5, bounce: 0.3)) { connection = .connected }
            models = try? await api.models()
            voiceList = try? await api.voices()
            if let m = models, let c = modelChoice, !m.models.contains(c) { modelChoice = nil }   // gone from the server
            indicators = try await api.indicators()
        } catch {
            health = nil
            serverError = error.localizedDescription
            withAnimation(.spring(duration: 0.5)) { connection = .offline(error.localizedDescription) }
        }
    }

    /// Connect, and while the server can't be reached try again every few seconds, gently, without the user doing
    /// anything. Runs for the life of the app.
    func keepConnected() async {
        await connect()
        while !Task.isCancelled {
            if case .offline = connection {
                nextRetry = Date().addingTimeInterval(8)
                try? await Task.sleep(for: .seconds(8))
                await connect()
            } else {
                try? await Task.sleep(for: .seconds(30))
                if connection == .connected, let api, (try? await api.health()) == nil { await connect() }
            }
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
        if let api { Task { await api.cancel(ex.requestId) } }
        endActivity(ex, answer: nil, dismiss: true)
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
                                 temperature: temperatureChoice, reasoning: reasoningChoice, fast: fast,
                                 requestId: "")
        let ex = Exchange(question: q, tableId: tableId, lang: lang, fast: fast)
        ex.spoken = spoken
        withAnimation(.smooth) { exchanges.append(ex) }
        startActivity(ex)
        busy = true
        askNotificationsOnce()
        // a little extra time if the user leaves the app mid-answer; the server finishes regardless
        var bg = UIBackgroundTaskIdentifier.invalid
        bg = UIApplication.shared.beginBackgroundTask(withName: "answer") { UIApplication.shared.endBackgroundTask(bg) }
        defer { UIApplication.shared.endBackgroundTask(bg) }
        do {
            for try await event in api.ask(request.with(requestId: ex.requestId)) {
                switch event {
                case .step(let s):
                    ex.steps.append(s)
                    if s.name != "context" { ex.stepStarted = Date() }  // the next step's clock starts now
                    updateActivity(ex, s)
                case .answer(let a):
                    deliver(a, to: ex, api: api)
                }
            }
        } catch {
            if Task.isCancelled { return }   // replaced by the quick answer
            await collect(ex, api: api)      // the line was cut, most likely by the phone sleeping
        }
        busy = false
    }

    /// Show an answer, keep it, read it aloud if asked by voice, tell the user if they are elsewhere, fetch follow-ups.
    private func deliver(_ a: Answer, to ex: Exchange, api: API) {
        ex.reconnecting = false
        let onLockScreen = activities[ex.requestId] != nil   // the Live Activity already shows the answer
        endActivity(ex, answer: a)
        withAnimation(.snappy) { ex.answer = a }
        save()
        if ex.spoken && autoSpeak && a.status != "error" && UIApplication.shared.applicationState == .active { speak(ex) }
        if UIApplication.shared.applicationState != .active && !onLockScreen { notifyReady(ex, a) }
        if a.status == "answered" {
            Task {
                do { let s = try await api.suggest(a); withAnimation(.snappy) { ex.suggestions = s } }
                catch { ex.suggestionsFailed = true }
            }
        }
    }

    /// The stream was cut: the server goes on and keeps the answer, so ask for it until it is ready (or 15 minutes
    /// pass). Asleep, the app simply resumes this loop when it wakes.
    private func collect(_ ex: Exchange, api: API) async {
        ex.reconnecting = true
        let started = Date()
        while Date().timeIntervalSince(started) < 15 * 60 {
            if Task.isCancelled { return }
            do {
                if let a = try await api.result(ex.requestId) { deliver(a, to: ex, api: api); return }
            } catch APIError.http(let code, _) where code == 404 || code == 410 {
                break   // the server no longer has it (restarted, or the question was stopped)
            } catch {}  // no connection yet: keep trying
            try? await Task.sleep(for: .seconds(2))
        }
        ex.reconnecting = false
        ex.error = ex.lang == .is ? "Svarið tapaðist á leiðinni. Reyndu aftur." : "The answer was lost on the way. Try again."
        endActivity(ex, answer: nil)
    }

    // MARK: Live Activities: each question's steps on the lock screen and in the Dynamic Island

    private static let stepOrder = ["plan", "retrieve", "select", "fetch", "compose"]

    private func startActivity(_ ex: Exchange) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        // answers already read make way for the new question
        for old in Activity<QuestionActivity>.activities where old.activityState == .ended {
            Task { await old.end(nil, dismissalPolicy: .immediate) }
        }
        let attrs = QuestionActivity(question: ex.question, isIcelandic: ex.lang == .is, started: Date())
        let state = QuestionActivity.ContentState(step: 0, stepName: Words(lang: ex.lang).steps[0], detail: "")
        activities[ex.requestId] = try? Activity.request(attributes: attrs, content: .init(state: state, staleDate: nil))
    }

    private func updateActivity(_ ex: Exchange, _ s: Step) {
        guard let act = activities[ex.requestId], Self.stepOrder.contains(s.name) else { return }
        let done = Set(ex.steps.map(\.name)).intersection(Self.stepOrder).count
        let step = min(done, QuestionActivity.steps - 1)
        let w = Words(lang: ex.lang)
        var detail = ""
        switch s.name {
        case "retrieve": detail = firstGroups(#"^(\S+) from"#, s.detail)?[1] ?? ""
        case "fetch": detail = w.numbers(Int(firstGroups(#"^(\d+) rows"#, s.detail)?[1] ?? "0") ?? 0)
        default: break
        }
        let state = QuestionActivity.ContentState(step: step, stepName: w.steps[step], detail: detail)
        Task { await act.update(.init(state: state, staleDate: nil)) }
    }

    /// The answer stays on the lock screen for a while; a stopped question goes at once.
    private func endActivity(_ ex: Exchange, answer: Answer?, dismiss: Bool = false) {
        guard let act = activities.removeValue(forKey: ex.requestId) else { return }
        let w = Words(lang: ex.lang)
        let state = QuestionActivity.ContentState(step: QuestionActivity.steps, stepName: answer == nil ? w.failed : "",
                                                  detail: "", answer: answer.map { prettyPeriods($0.text, ex.lang) },
                                                  failed: answer == nil || answer?.status == "error")
        Task {
            await act.end(.init(state: state, staleDate: nil),
                          dismissalPolicy: dismiss ? .immediate : .after(Date().addingTimeInterval(15 * 60)))
        }
    }

    // MARK: "Your answer is ready" when the app is in the background

    private func askNotificationsOnce() {
        guard !UserDefaults.standard.bool(forKey: "askedNotifications") else { return }
        UserDefaults.standard.set(true, forKey: "askedNotifications")
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func notifyReady(_ ex: Exchange, _ a: Answer) {
        let c = UNMutableNotificationContent()
        c.title = ex.lang == .is ? "Svarið er tilbúið" : "Your answer is ready"
        c.subtitle = ex.question
        c.body = prettyPeriods(a.text, ex.lang)
        c.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: ex.requestId, content: c, trigger: nil))
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

    /// Encoding every saved conversation (rows, steps and all) takes long enough to freeze the screen just as an
    /// answer arrives, so it happens off the main thread, one write at a time, in order.
    private static let writer = DispatchQueue(label: "ragstofan.history", qos: .utility)
    private func write() {
        let snapshot = conversations, file = Self.file
        Self.writer.async {
            if let data = try? JSONEncoder().encode(snapshot) { try? data.write(to: file, options: .atomic) }
        }
    }

    /// Store the conversation on screen, newest first, after each answer.
    private func save() {
        if privateMode { return }   // private: nothing is kept
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

    /// Conversations whose questions or answers contain the search text (any case, any accents).
    func search(_ text: String) -> [Conversation] {
        let q = text.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return conversations }
        return conversations.filter { c in
            c.exchanges.contains { $0.question.localizedStandardContains(q) || $0.answer.text.localizedStandardContains(q) }
        }
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
