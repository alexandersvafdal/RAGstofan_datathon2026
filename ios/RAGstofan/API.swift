// Talks to the RAGstofan server (hagbot/api.py): POST /api/ask/stream for answers, GET /api/indicators for the
// dashboard, GET /api/health for the status line.
import Foundation

enum APIError: LocalizedError {
    case badServer(String)
    case http(Int, String)
    case streamEnded

    var errorDescription: String? {
        switch self {
        case .badServer(let s): return "“\(s)” is not a server address."
        case .http(let code, let body): return "The server answered \(code). \(body.prefix(200))"
        case .streamEnded: return "The answer stream ended early."
        }
    }
}

struct API {
    let base: URL

    init(server: String) throws {
        var s = server.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "http://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        guard let url = URL(string: s), url.host != nil else { throw APIError.badServer(server) }
        base = url
    }

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    private static let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 330   // the server allows the model 300 s
        c.timeoutIntervalForResource = 600
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: c)
    }()

    func health() async throws -> Health {
        try await get("api/health")
    }

    /// The voice service runs beside the server on port 8001 (voice/server.py on the Spark).
    var voice: URL {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        c.port = 8001
        c.path = ""
        return c.url!
    }

    /// Speech to text: the recording goes up as a file, the words come back.
    func stt(_ file: URL, language: String?) async throws -> String {
        let boundary = "ragstofan-\(UUID().uuidString)"
        var body = Data()
        func part(_ s: String) { body.append(s.data(using: .utf8)!) }
        if let language {
            part("--\(boundary)\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n\(language)\r\n")
        }
        part("--\(boundary)\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"question.m4a\"\r\nContent-Type: audio/mp4\r\n\r\n")
        body.append(try Data(contentsOf: file))
        part("\r\n--\(boundary)--\r\n")
        var req = URLRequest(url: voice.appendingPathComponent("stt"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 60
        let (data, resp) = try await Self.session.upload(for: req, from: body)
        try check(resp, data)
        struct Heard: Decodable { let text: String }
        return try Self.decoder.decode(Heard.self, from: data).text
    }

    /// Text to speech: a WAV in the answer's language.
    func tts(_ text: String, language: String) async throws -> Data {
        var req = URLRequest(url: voice.appendingPathComponent("tts"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["text": text, "language": language])
        req.timeoutInterval = 60
        let (data, resp) = try await Self.session.data(for: req)
        try check(resp, data)
        return data
    }

    func models() async throws -> ModelList {
        try await get("api/models")
    }

    func indicators() async throws -> Indicators {
        try await get("api/indicators")
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, resp) = try await Self.session.data(from: base.appendingPathComponent(path))
        try check(resp, data)
        return try Self.decoder.decode(T.self, from: data)
    }

    private func check(_ resp: URLResponse, _ data: Data) throws {
        if let h = resp as? HTTPURLResponse, !(200..<300).contains(h.statusCode) {
            throw APIError.http(h.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
    }

    /// Steps as they finish, then the answer: one JSON object per line.
    func ask(_ request: AskRequest) -> AsyncThrowingStream<StreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = URLRequest(url: base.appendingPathComponent("api/ask/stream"))
                    req.httpMethod = "POST"
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.httpBody = try Self.encoder.encode(request)
                    let (bytes, resp) = try await Self.session.bytes(for: req)
                    if let h = resp as? HTTPURLResponse, !(200..<300).contains(h.statusCode) {
                        throw APIError.http(h.statusCode, "")
                    }
                    for try await line in bytes.lines {
                        guard !line.isEmpty, let data = line.data(using: .utf8) else { continue }
                        let kind = try Self.decoder.decode(Kind.self, from: data)
                        if kind.type == "step" {
                            continuation.yield(.step(try Self.decoder.decode(Step.self, from: data)))
                        } else if kind.type == "answer" {
                            let wrapped = try Self.decoder.decode(Wrapped.self, from: data)
                            continuation.yield(.answer(wrapped.answer))
                            continuation.finish()
                            return
                        }
                    }
                    throw APIError.streamEnded
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private struct Kind: Decodable { let type: String }
    private struct Wrapped: Decodable { let answer: Answer }
}
