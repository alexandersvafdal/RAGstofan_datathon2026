// Numbers, periods and interface text in English and Icelandic, as the website writes them.
import Foundation

enum Lang: String, CaseIterable, Identifiable {
    case en, `is`
    var id: String { rawValue }
}

enum LangChoice: String, CaseIterable, Identifiable {
    case auto, `is`, en
    var id: String { rawValue }
    var title: String { self == .auto ? "Auto" : rawValue.uppercased() }
}

let monthNames: [Lang: [String]] = [
    .en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    .is: ["jan.", "feb.", "mar.", "apr.", "maí", "jún.", "júl.", "ágú.", "sep.", "okt.", "nóv.", "des."],
]

/// "2026M09" -> "Sep 2026", "2026Q2" -> "Q2 2026"; anything else unchanged.
func fmtPeriod(_ p: String, _ lang: Lang) -> String {
    let s = p as NSString
    if let m = try? NSRegularExpression(pattern: "^(\\d{4})M(\\d{2})$").firstMatch(in: p, range: NSRange(location: 0, length: s.length)),
       let mm = Int(s.substring(with: m.range(at: 2))), (1...12).contains(mm) {
        return "\(monthNames[lang]![mm - 1]) \(s.substring(with: m.range(at: 1)))"
    }
    if let m = try? NSRegularExpression(pattern: "^(\\d{4})Q(\\d)$").firstMatch(in: p, range: NSRange(location: 0, length: s.length)) {
        let y = s.substring(with: m.range(at: 1)), q = s.substring(with: m.range(at: 2))
        return lang == .is ? "\(q). ársfj. \(y)" : "Q\(q) \(y)"
    }
    return p
}

/// Period codes anywhere in a sentence read naturally.
func prettyPeriods(_ text: String, _ lang: Lang) -> String {
    guard let rx = try? NSRegularExpression(pattern: "\\b\\d{4}(M\\d{2}|Q\\d)\\b") else { return text }
    var out = text
    for m in rx.matches(in: text, range: NSRange(text.startIndex..., in: text)).reversed() {
        if let r = Range(m.range, in: out) { out.replaceSubrange(r, with: fmtPeriod(String(out[r]), lang)) }
    }
    return out
}

/// 394,324 and 5.9 in English; 394.324 and 5,9 in Icelandic.
func fmtNum(_ v: Double, percent: Bool = false, digits: Int? = nil, _ lang: Lang) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.locale = Locale(identifier: "en_US")
    if percent { f.minimumFractionDigits = 1; f.maximumFractionDigits = 1 }
    else if let digits { f.maximumFractionDigits = digits }
    else { f.maximumFractionDigits = (abs(v) < 100 && v.rounded() != v) ? 1 : 0 }
    let s = f.string(from: NSNumber(value: v)) ?? "\(v)"
    guard lang == .is else { return s }
    return String(s.map { $0 == "," ? "." : $0 == "." ? "," : $0 })
}

/// The capture groups of the first match, group 0 being the whole match.
func firstGroups(_ pattern: String, _ s: String) -> [String]? {
    guard let rx = try? NSRegularExpression(pattern: pattern),
          let m = rx.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
    return (0..<m.numberOfRanges).map { i in Range(m.range(at: i), in: s).map { String(s[$0]) } ?? "" }
}

func allGroups(_ pattern: String, _ s: String) -> [[String]] {
    guard let rx = try? NSRegularExpression(pattern: pattern) else { return [] }
    return rx.matches(in: s, range: NSRange(s.startIndex..., in: s)).map { m in
        (0..<m.numberOfRanges).map { i in Range(m.range(at: i), in: s).map { String(s[$0]) } ?? "" }
    }
}

func replaceFirst(_ s: String, _ pattern: String, _ with: String) -> String {
    guard let r = s.range(of: pattern, options: .regularExpression) else { return s }
    return s.replacingCharacters(in: r, with: with)
}

/// "value {'Month': ['2026M09'], 'Item': ['Annual change, %']}" -> "Month: Sep 2026 · Item: Annual change, %"
func readSelection(_ detail: String, _ lang: Lang) -> String {
    allGroups(#"'([^']+)': \[([^\]]*)\]"#, detail).prefix(4).map { g in
        let vals = allGroups(#"'([^']*)'"#, g[2]).map { prettyPeriods($0[1], lang) }
        return "\(g[1]): " + (vals.count > 3 ? "\(vals[0]) … \(vals[vals.count - 1]) (\(vals.count))" : vals.joined(separator: ", "))
    }.joined(separator: " · ")
}

func fmtMs(_ ms: Int) -> String { ms >= 1000 ? String(format: "%.1f s", Double(ms) / 1000) : "\(ms) ms" }

/// Guess the language of a question the way the website does: Icelandic letters mean Icelandic.
func guessLang(_ text: String) -> Lang {
    text.range(of: "[þðæöáéíóúý]", options: [.regularExpression, .caseInsensitive]) != nil ? .is : .en
}

struct Words {
    let lang: Lang
    private func t(_ en: String, _ is_: String) -> String { lang == .is ? is_ : en }

    var eyebrow: String { t("Statistics Iceland · Gagnaþon 2026", "Hagstofa Íslands · Gagnaþon 2026") }
    var hello: String { t("Iceland, in numbers.", "Ísland í tölum.") }
    var sub: String { t("Ask anything about Iceland's official statistics. Every answer comes with the figure, its unit and the table it came from.",
                        "Spurðu um opinbera tölfræði Íslands. Hverju svari fylgir talan, eining hennar og taflan sem hún kemur úr.") }
    var today: String { t("The latest numbers", "Nýjustu tölur") }
    var asof: String { t("Data as of", "Gögn frá") }
    var placeholder: String { t("Ask about Icelandic statistics", "Spyrðu um íslenska tölfræði") }
    var steps: [String] { lang == .is ? ["Skil spurninguna", "Finn réttu töfluna", "Vel flokkana", "Les tölurnar", "Skrifa svarið"]
                                      : ["Understanding the question", "Finding the right table", "Choosing the categories", "Reading the numbers", "Writing the answer"] }
    var how: String { t("How I got this", "Hvernig svarið varð til") }
    var data: String { t("Data", "Gögn") }
    var shortlist: String { t("Shortlist", "Stuttlisti") }
    var source: String { t("Source", "Heimild") }
    var checked: String { t("Numbers checked against the table", "Tölur bornar saman við töfluna") }
    var calc: String { t("Calculated", "Reiknað") }
    var failed: String { t("Something went wrong", "Eitthvað fór úrskeiðis") }
    var retry: String { t("Try again", "Reyna aftur") }
    var pts: String { t("pts", "pst.") }
    var chosen: String { t("chosen", "valin") }
    var settings: String { t("Settings", "Stillingar") }
    var server: String { t("Server", "Þjónn") }
    var colour: String { t("Colour", "Litir") }
    var appearance: String { t("Appearance", "Útlit") }
    var language: String { t("Answer language", "Tungumál svara") }
    var about: String { t("About RAGstofan", "Um RAGstofuna") }
    var newChat: String { t("New conversation", "Nýtt samtal") }
    func read(_ q: String) -> String { t("Read as: \(q)", "Skilið sem: \(q)") }
    func split(_ n: Int) -> String { t("Split into \(n) questions", "Skipt í \(n) spurningar") }
    func followup(_ q: String) -> String { t("Follow-up, read as: \(q)", "Framhaldsspurning, skilin sem: \(q)") }
    func searched(_ n: Int, _ id: String) -> String { t("Searched \(fmtNum(Double(n), lang)) tables → picked \(id)", "Leitaði í \(fmtNum(Double(n), lang)) töflum → valdi \(id)") }
    func numbers(_ n: Int) -> String { lang == .is ? "\(n) \(n == 1 ? "tala lesin" : "tölur lesnar")" : "\(n) \(n == 1 ? "number" : "numbers") read" }
    var template: String { t("Written from the numbers directly", "Skrifað beint upp úr tölunum") }
    func votes(_ v: String) -> String { t("Votes: \(v)", "Atkvæði: \(v)") }
    func via(_ tier: String) -> String {
        let en = ["keywords": "found through the keyword dictionary", "search": "found by keyword and semantic search",
                  "all tables": "picked by the model from every table", "user choice": "none confirmed, so you choose"]
        let is_ = ["keywords": "fundin með orðasafninu", "search": "fundin með leitarorða- og merkingarleit",
                   "all tables": "valin af líkaninu úr öllum töflum", "user choice": "engin staðfest, svo þú velur"]
        return (lang == .is ? is_ : en)[tier] ?? tier
    }
    func signal(_ s: String) -> String {
        let parts = s.split(separator: " ", maxSplits: 1).map(String.init)
        let names: [String: (String, String)] = ["dictionary": ("dictionary", "orðasafn"), "bm25": ("keywords", "leitarorð"),
                                                  "e5": ("meaning (e5)", "merking (e5)"), "bge-m3": ("meaning (bge-m3)", "merking (bge-m3)")]
        let n = names[parts[0]].map { lang == .is ? $0.1 : $0.0 } ?? parts[0]
        return parts.count > 1 ? "\(n) \(parts[1])" : n
    }
    func viewName(_ v: String) -> String {
        let en = ["line": "Line", "stacked": "Stacked", "heatmap": "Heatmap", "bars": "Bars", "lollipop": "Lollipop",
                  "donut": "Donut", "waffle": "Waffle", "slope": "Slope"]
        let is_ = ["line": "Lína", "stacked": "Staflað", "heatmap": "Hitakort", "bars": "Súlur", "lollipop": "Sleikjó",
                   "donut": "Kleinuhringur", "waffle": "Vaffla", "slope": "Halli"]
        return (lang == .is ? is_ : en)[v] ?? v
    }
}


// "Value of index", "Index", "Total": words that name the table's layout, not the number, are left off tile labels
func cleanLabel(_ text: String) -> String {
    let filler: Set<String> = ["value of index", "index", "total", "alls", "samtals", "gildi vísitölu", "vísitala", "value"]
    return text.components(separatedBy: ", ").flatMap { $0.components(separatedBy: " · ") }
        .filter { !$0.isEmpty && !filler.contains($0.lowercased().trimmingCharacters(in: .whitespaces)) }
        .joined(separator: ", ")
}

/// "Launavísitala eftir mánuðum frá 1989" -> "Launavísitala", as the server shortens legend names.
func shortTitle(_ title: String) -> String {
    let pattern = #"\s+-\s+|,|\(|\s+(from|by|and changes|frá|eftir|og breytingar)\b|\s+\d{4}"#
    guard let r = title.range(of: pattern, options: .regularExpression) else { return title }
    let head = title[..<r.lowerBound].trimmingCharacters(in: .whitespaces)
    return head.isEmpty ? title : head
}

/// A number as the table has it: 1.149,3 keeps its decimal, 394.324 stays whole.
func fmtExact(_ v: Double, _ lang: Lang) -> String { fmtNum(v, digits: v.rounded() == v ? 0 : 1, lang) }
