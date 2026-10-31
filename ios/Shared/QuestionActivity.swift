// What a question's Live Activity shows, shared by the app (which starts and updates it) and the widget extension
// (which draws it on the lock screen and in the Dynamic Island).
import ActivityKit
import Foundation

struct QuestionActivity: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var step: Int           // 0 understanding · 1 finding the table · 2 categories · 3 numbers · 4 writing · 5 done
        var stepName: String    // in the question's language
        var detail: String      // what the step found, e.g. "VIN00001"
        var answer: String?     // the answer once it is ready
        var failed = false
    }

    var question: String
    var isIcelandic: Bool
    var started: Date

    static let steps = 5
}
