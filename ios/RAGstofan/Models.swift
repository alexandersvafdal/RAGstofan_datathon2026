// The server's JSON, as hagbot/schemas.py and hagbot/indicators.py define it. Keys arrive in snake_case and are
// decoded with .convertFromSnakeCase, so `text_source` becomes `textSource`.
import Foundation

struct TableRef: Codable, Hashable { let id: String; let title: String; let url: String; var updated: String? = nil }

struct Fact: Codable, Hashable { let label: String; let value: Double; let unit: String; let formatted: String }

struct Computation: Codable, Hashable { let op: String; let expression: String; let result: Double; let unit: String }

struct ChartPoint: Codable, Hashable { let x: String; let y: Double; let series: String? }

struct ChartSpec: Codable, Hashable {
    let type: String          // "kpi", "line" or "bar"
    let title: String
    let xLabel: String
    let yLabel: String
    let unit: String
    let data: [ChartPoint]
}

struct Row: Codable, Hashable {
    let key: [String: String]
    let labels: [String: String]
    let value: Double?
}

struct RankedTable: Codable, Hashable { let id: String; let title: String; let score: Double; let signals: [String] }

struct AnswerPart: Codable, Hashable {
    let subQuestion: String
    let table: TableRef
    let selection: [String: [String]]
    let rows: [Row]
    let unit: String
    let computation: Computation?
    let facts: [Fact]
    let chart: ChartSpec?
    let note: String?
    let ranking: [RankedTable]?
    var drill: [Drill]? = nil   // categories that open further, each with the question that lists its subcategories
}

struct Drill: Codable, Hashable {
    let label: String; let question: String; let tableId: String
    var kind: String? = nil   // "subcategories" (default) or "alternative": a category the question did not name
}

struct ClarifyOption: Codable, Hashable { let label: String; let tableId: String }
struct Clarification: Codable, Hashable { let message: String; let options: [ClarifyOption] }

struct Step: Codable, Hashable { let name: String; let ms: Int; let detail: String }

struct Answer: Codable, Hashable {
    let question: String
    let language: String
    let status: String        // "answered", "clarify" or "error"
    let text: String
    let parts: [AnswerPart]
    var comparison: ChartSpec? = nil   // series from different tables on one chart, each set to 100 at the start
    let clarification: Clarification?
    let sources: [TableRef]
    let steps: [Step]
    let textSource: String
    let verdict: String?      // the answer read back against the question: ok / doubtful; older servers send none
    let llmCalls: Int
    let totalMs: Int
    let model: String
}

/// One earlier question and answer, sent back so "what about last year?" can be understood.
struct Turn: Codable, Hashable { let question: String; let answer: String; let tables: [String] }

struct AskRequest: Encodable {
    let question: String
    let language: String?
    let tableId: String?
    let history: [Turn]
    let model: String?        // nil: the server's default, the best one measured
    let fast: Bool            // "Answer now": the fast model
    let requestId: String     // the server finishes and keeps the answer under this if the phone drops the line
}

extension AskRequest {
    func with(requestId: String) -> AskRequest {
        AskRequest(question: question, language: language, tableId: tableId, history: history, model: model,
                   fast: fast, requestId: requestId)
    }
}

/// A question asked earlier with a request id: still running, or its answer.
struct AskResult: Decodable { let status: String; let answer: Answer? }

/// A follow-up question that leads on from an answer: compare, related, deeper or wider.
struct Suggestion: Codable, Hashable { let kind: String; let question: String }
struct SuggestionList: Codable { let questions: [Suggestion] }

/// A voice answers can be read in.
struct VoiceOption: Codable, Hashable, Identifiable { let id: String; let name: String; let engine: String }
struct VoiceList: Codable { let voices: [String: [VoiceOption]]; let `default`: [String: String] }

/// The models a question can go to; the default is the server's best. Older servers send only the first two fields.
struct ModelList: Codable {
    let `default`: String
    let models: [String]
    let fast: String?
    let sizes: [String: String]?   // model → large / medium / small / tiny, from the parameter count in the tag
}

struct Health: Codable { let model: String; let dataSource: String; let tablesInCatalog: Int }

struct LangText: Codable, Hashable {
    let en: String
    let `is`: String
    func callAsFunction(_ lang: Lang) -> String { lang == .is ? self.is : en }
}

struct Indicator: Codable, Hashable, Identifiable {
    let id: String
    let label: LangText
    let unit: LangText
    let value: Double
    let period: String
    let previous: Double?
    let previousPeriod: String?
    let series: [ChartPoint]
    let table: [String: String]
    let question: LangText
}

struct Indicators: Codable { let dataAsOf: String; let indicators: [Indicator] }

/// A line of the answer stream: a pipeline step the moment it finishes, then the answer.
enum StreamEvent {
    case step(Step)
    case answer(Answer)
}
