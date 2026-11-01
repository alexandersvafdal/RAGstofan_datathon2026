// The answer card (frontend renderAnswer): text, headline figures, charts, notes, sources, and the details behind it.
import SwiftUI
import UIKit

private struct Figure: Identifiable {
    let id: Int; let value: String; let number: Double; let unit: String; let label: String; let calc: String?
    var from: String? = nil      // "frá 545 (jan. 2016)": where a series started
    var change: Double? = nil    // % for a level, percentage points for a rate
    var points = false           // the change is in percentage points
}

/// Each part of the card fades and rises into place a moment after the one above it.
struct Reveal: ViewModifier {
    let step: Int
    let shown: Bool
    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 12)
            .blur(radius: shown ? 0 : 3)
            .animation(.spring(duration: 0.6, bounce: 0.18).delay(Double(step) * 0.07), value: shown)
    }
}

extension View {
    func reveal(_ step: Int, _ shown: Bool) -> some View { modifier(Reveal(step: step, shown: shown)) }

    /// Long-press to copy, with a Copy item in the menu.
    func copyable(_ text: String, _ lang: Lang) -> some View {
        contextMenu {
            Button { UIPasteboard.general.string = text } label: {
                Label(lang == .is ? "Afrita" : "Copy", systemImage: "doc.on.doc")
            }
        }
    }
}

struct AnswerCard: View {
    @Environment(\.pal) private var pal
    let a: Answer
    let ask: Ask
    var fast = false
    var exchange: Exchange?
    @Environment(AppModel.self) private var model
    @State private var picks: [String]
    @State private var shown = false
    @State private var copied = 0

    init(a: Answer, ask: @escaping Ask, fast: Bool = false, exchange: Exchange? = nil) {
        self.a = a
        self.ask = ask
        self.fast = fast
        self.exchange = exchange
        _picks = State(initialValue: followupPicks(a))
    }

    private var L: Lang { a.language == "is" ? .is : .en }
    private var secs: String { String(format: "%.1f s", Double(a.totalMs) / 1000) }
    /// The answer as it reads, with its sources, for the clipboard.
    private var copyText: String {
        let src = a.sources.map { "\($0.id) \($0.url)" }.joined(separator: "\n")
        return prettyPeriods(a.text, L) + (src.isEmpty ? "" : "\n\n" + (L == .is ? "Heimild: " : "Source: ") + src)
    }

    var body: some View {
        let w = Words(lang: L)
        Card {
            VStack(alignment: .leading, spacing: 18) {
                SentenceText(text: prettyPeriods(a.text, L), color: a.status == "error" ? Color.red : pal.text)
                    .font(.system(size: 17))
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .copyable(copyText, L)
                    .reveal(0, shown)

                if a.status == "clarify", let c = a.clarification {
                    VStack(spacing: 8) {
                        ForEach(Array(c.options.enumerated()), id: \.offset) { i, o in
                            Button { ask(a.question, o.tableId) } label: {
                                HStack(spacing: 10) {
                                    Text(o.label).foregroundStyle(pal.text).multilineTextAlignment(.leading)
                                    Spacer()
                                    Text(o.tableId).font(.caption.monospaced()).foregroundStyle(pal.text3)
                                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(pal.text3)
                                }
                                .padding(14)
                                .background(pal.soft, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                            }
                            .buttonStyle(.plain)
                            .reveal(1 + i, shown)
                        }
                    }
                }

                let figs = figures(w)
                if !figs.isEmpty {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 12, alignment: .topLeading)], alignment: .leading, spacing: 12) {
                        ForEach(figs) { f in
                            FigureView(fig: f, lang: L, shown: shown)
                                .copyable("\(f.value)\(f.unit == "%" ? "%" : f.unit.isEmpty ? "" : " " + f.unit) · \(f.label)", L)
                                .reveal(1 + f.id, shown)
                        }
                    }
                }

                // series from different tables on one chart; the separate line charts it replaces are left out
                if let cmp = a.comparison {
                    ChartBlock(chart: cmp, comp: nil, lang: L, compare: true).reveal(3, shown)
                }
                ForEach(Array(a.parts.enumerated()), id: \.offset) { i, p in
                    if let c = p.chart, c.type != "kpi", c.data.count > 1, !(a.comparison != nil && c.type == "line") {
                        ChartBlock(chart: c, comp: p.computation, lang: L).reveal(3 + i, shown)
                    }
                }

                // categories that open further: each asks for its own subcategories from the same table
                let allDrills = a.parts.flatMap { $0.drill ?? [] }
                ForEach(["subcategories", "alternative"], id: \.self) { kind in
                let drills = allDrills.filter { ($0.kind ?? "subcategories") == kind }
                if !drills.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(kind == "alternative" ? (L == .is ? "SAMA SPURNING FYRIR ANNAN FLOKK" : "SAME QUESTION FOR ANOTHER CATEGORY")
                             : (L == .is ? "SKOÐA UNDIRFLOKKA" : "OPEN A CATEGORY"))
                            .font(.caption2.weight(.semibold)).tracking(0.8).foregroundStyle(pal.text3)
                        Flow(spacing: 6) {
                            ForEach(drills, id: \.self) { d in
                                Button { ask(d.question, d.tableId) } label: {
                                    HStack(spacing: 3) {
                                        Text(d.label).lineLimit(1)
                                        Image(systemName: "chevron.right").font(.caption2.weight(.bold)).foregroundStyle(pal.accent)
                                    }
                                    .font(.footnote).foregroundStyle(pal.text2)
                                    .padding(.horizontal, 11).padding(.vertical, 6)
                                    .overlay(Capsule().stroke(pal.text.opacity(0.12)))
                                    .contentShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    .reveal(4, shown)
                }
                }

                if fast {
                    Label(L == .is ? "Svarað í flýti með hraða líkaninu, án hugsunar." : "Answered quickly with the fast model, thinking off.",
                          systemImage: "bolt.fill")
                        .font(.footnote).foregroundStyle(pal.text2)
                        .reveal(4, shown)
                }
                ForEach(Array(a.parts.compactMap(\.note).filter { !$0.isEmpty }.enumerated()), id: \.offset) { _, note in
                    Label { Text(prettyPeriods(note, L)) } icon: { Image(systemName: "info.circle") }
                        .font(.footnote)
                        .foregroundStyle(pal.text2)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(pal.warn, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .copyable(prettyPeriods(note, L), L)
                        .reveal(4, shown)
                }

                // where the numbers come from: the table in the answer's language, a link, and how recent its data is
                if !a.sources.isEmpty {
                    VStack(spacing: 8) { ForEach(a.sources, id: \.id) { SourceCard(s: $0, lang: L) } }.reveal(5, shown)
                }

                // One quiet line under the answer: whether the numbers were checked and how long it took, then the actions
                HStack(alignment: .center, spacing: 4) {
                    Flow(spacing: 10) {
                        if a.textSource == "model" && a.verdict != "doubtful" {
                            Label(L == .is ? "Yfirfarið" : "Checked", systemImage: "checkmark.seal")
                                .labelStyle(.titleAndIcon)
                                .accessibilityLabel(w.checked)
                        }
                        if a.totalMs > 0 { Text(secs).monospacedDigit() }
                    }
                    .font(.caption)
                    .foregroundStyle(pal.text3)
                    Spacer(minLength: 8)
                    if let ex = exchange, a.status != "error" {
                        // read aloud by the Spark's Piper voice, in the answer's language
                        let on = model.voice.speaking == ex.id.uuidString, loading = model.voice.loadingSpeech == ex.id.uuidString
                        Button { model.speak(ex) } label: {
                            Group {
                                if loading { ProgressView().controlSize(.small).tint(pal.accent) }
                                else if on { SpeakingBars().scaleEffect(0.75) }
                                else { Image(systemName: "speaker.wave.2") }
                            }
                            .frame(width: 34, height: 30)
                            .foregroundStyle(on ? pal.accent : pal.text3)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(on ? (L == .is ? "Stöðva" : "Stop") : (L == .is ? "Hlusta" : "Listen"))
                    }
                    Button {
                        UIPasteboard.general.string = copyText
                        copied += 1
                    } label: {
                        Image(systemName: copied > 0 ? "checkmark" : "doc.on.doc")
                            .contentTransition(.symbolEffect(.replace))
                            .frame(width: 34, height: 30)
                            .foregroundStyle(copied > 0 ? pal.accent : pal.text3)
                            .contentShape(Rectangle())
                            .animation(.snappy, value: copied)
                    }
                    .buttonStyle(.plain)
                    .sensoryFeedback(.success, trigger: copied)
                    .accessibilityLabel(L == .is ? "Afrita" : "Copy")
                }
                .font(.footnote)
                .reveal(5, shown)

                // The working (data, shortlist, steps) behind one row, for whoever wants to check it
                if !a.parts.isEmpty || !a.steps.isEmpty {
                    Disclosure(title: L == .is ? "Nánar" : "Details") {
                        VStack(alignment: .leading, spacing: 20) {
                            if !a.parts.isEmpty {
                                DetailSection(title: w.data) { DataTables(parts: a.parts, lang: L) }
                            }
                            if a.parts.contains(where: { !($0.ranking ?? []).isEmpty }) {
                                DetailSection(title: w.shortlist) { Shortlist(parts: a.parts, lang: L) }
                            }
                            if !a.steps.isEmpty {
                                DetailSection(title: "\(w.how) · \(a.llmCalls) \(L == .is ? "köll" : "calls")") { StepsTimeline(a: a, lang: L) }
                            }
                        }
                    }
                    .reveal(6, shown)
                }

                Group {
                    if let ex = exchange, a.status == "answered", !ex.suggestionsFailed {
                        if let list = ex.suggestions, !list.isEmpty {
                            VStack(spacing: 6) {
                                ForEach(Array(list.enumerated()), id: \.offset) { i, s in
                                    SuggestionPill(s: s, lang: L, delay: Double(i) * 0.38) { ask(s.question, nil) }
                                }
                            }
                        } else if ex.suggestions == nil {
                            GhostPills()   // being written, just after the answer
                        }
                    } else {
                        Flow(spacing: 8) {
                            ForEach(Array(picks.enumerated()), id: \.offset) { i, q in
                                Button(q) { ask(q, nil) }.buttonStyle(PillStyle()).reveal(7 + i, shown)
                            }
                        }
                    }
                }
                .padding(.top, 2)
            }
        }
        .onAppear { shown = true }
    }

    /// A calculation: its result. A series: where it started and where it is now. Several lines: the latest of each.
    private func figures(_ w: Words) -> [Figure] {
        var out: [Figure] = []
        for p in a.parts {
            // a series: one tile per line, where it ended, where it started and the change between
            if p.computation == nil, let c = p.chart, c.type == "line", c.data.count > 2, c.data.allSatisfy({ isPeriod($0.x) }) {
                let pct = p.unit == "%" || c.unit == "%"
                var order: [String] = [], by: [String: [ChartPoint]] = [:]
                for d in c.data { let k = d.series ?? ""; if by[k] == nil { order.append(k) }; by[k, default: []].append(d) }
                for k in order.prefix(4) {
                    let pts = by[k]!.sorted { $0.x < $1.x }
                    guard let first = pts.first, let last = pts.last else { continue }
                    let name = cleanLabel(k.isEmpty ? shortTitle(p.table.title) : k)
                    let change = pct ? last.y - first.y : (first.y != 0 ? (last.y - first.y) / abs(first.y) * 100 : nil)
                    out.append(Figure(id: out.count, value: fmtExact(last.y, L), number: last.y, unit: pct ? "%" : p.unit,
                                      label: [name, fmtPeriod(last.x, L)].filter { !$0.isEmpty }.joined(separator: " · "), calc: nil,
                                      from: "\(L == .is ? "frá" : "from") \(fmtExact(first.y, L))\(pct ? "%" : "") (\(fmtPeriod(first.x, L)))",
                                      change: change, points: pct))
                }
                continue
            }
            var keys: [String] = [], lines: [String: Fact] = [:]
            for f in p.facts {
                let k = replaceFirst(f.label, #"\b\d{4}(M\d{2}|Q\d)?\b,?\s*"#, "")
                if lines[k] == nil { keys.append(k) }
                lines[k] = f
            }
            let facts: [Fact]
            if p.computation != nil, let last = p.facts.last { facts = [last] }
            else if p.facts.count > 3 && keys.count > 1 { facts = keys.prefix(4).compactMap { lines[$0] } }
            else if p.facts.count > 3 { facts = [p.facts[0], p.facts[p.facts.count - 1]] }
            else { facts = Array(p.facts.prefix(3)) }
            for f in facts {
                let pct = f.unit == "%" || f.formatted.hasSuffix("%")
                out.append(Figure(id: out.count,
                                  value: f.formatted.replacingOccurrences(of: #"\s*%$"#, with: "", options: .regularExpression),
                                  number: f.value, unit: pct ? "%" : f.unit, label: cleanLabel(prettyPeriods(f.label, L)),
                                  calc: p.computation.map { "\(w.calc): \($0.expression)" }))
            }
        }
        return out
    }
}

/// A number that counts up from zero, then settles on the server's own formatting.
private struct CountUp: View, Animatable {
    var value: Double
    let digits: Int
    let lang: Lang
    var animatableData: Double {
        get { value }
        set { value = newValue }
    }
    // one formatter per number of decimals, not a new one on every frame of the count
    private static var formatters: [Int: NumberFormatter] = [:]
    private static func formatter(_ digits: Int) -> NumberFormatter {
        if let f = formatters[digits] { return f }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = Locale(identifier: "en_US")
        f.minimumFractionDigits = digits
        f.maximumFractionDigits = digits
        formatters[digits] = f
        return f
    }
    var body: some View {
        let s = Self.formatter(digits).string(from: NSNumber(value: value)) ?? ""
        return Text(lang == .is ? String(s.map { $0 == "," ? "." : $0 == "." ? "," : $0 }) : s)
    }
}

private struct FigureView: View {
    @Environment(\.pal) private var pal
    let fig: Figure
    let lang: Lang
    let shown: Bool
    @State private var counted = 0.0
    @State private var settled = false

    /// Decimals in the server's formatting: "5,9" and "5.9" have one.
    private var digits: Int {
        let sep: Character = lang == .is ? "," : "."
        guard let i = fig.value.lastIndex(of: sep) else { return 0 }
        return fig.value[fig.value.index(after: i)...].filter(\.isNumber).count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Group {
                    if settled { Text(fig.value) }
                    else { CountUp(value: counted, digits: digits, lang: lang) }
                }
                .font(.system(size: 30, weight: .semibold, design: .rounded)).monospacedDigit()
                .foregroundStyle(pal.text).minimumScaleFactor(0.6).lineLimit(1)
                if !fig.unit.isEmpty { Text(fig.unit).font(.subheadline).foregroundStyle(pal.text3).lineLimit(1) }
            }
            Text(fig.label).font(.caption).foregroundStyle(pal.text2).fixedSize(horizontal: false, vertical: true)
            if let from = fig.from {
                VStack(alignment: .leading, spacing: 4) {
                    if let d = fig.change {
                        let tint = d > 0 ? Color.green : d < 0 ? Color.red : pal.text2
                        Text("\(d > 0 ? "+" : d < 0 ? "−" : "±")\(fmtNum(abs(d), digits: 1, lang))\(fig.points ? (lang == .is ? " prst." : " pp") : "%")")
                            .font(.caption2.weight(.semibold)).monospacedDigit().foregroundStyle(tint)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(tint.opacity(0.14), in: Capsule())
                            .fixedSize()
                    }
                    Text(from).font(.caption2).monospacedDigit().foregroundStyle(pal.text3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 2)
            }
            if let c = fig.calc { Text(c).font(.caption2.monospaced()).foregroundStyle(pal.text3).fixedSize(horizontal: false, vertical: true) }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(pal.soft, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .onAppear {
            withAnimation(.easeOut(duration: 0.9).delay(0.15)) { counted = fig.number } completion: { settled = true }
        }
    }
}

/// Every number the answer was built from, one table per sub-question.
private struct DataTables: View {
    @Environment(\.pal) private var pal
    let parts: [AnswerPart]
    let lang: Lang

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(Array(parts.enumerated()), id: \.offset) { _, p in
                // JSON objects lose their order in Swift, so time comes first and the rest alphabetically
                let dims = (p.rows.first?.labels.keys.map { $0 } ?? []).sorted { a, b in
                    let ta = p.rows.first.map { isPeriod($0.labels[a] ?? "") } ?? false
                    let tb = p.rows.first.map { isPeriod($0.labels[b] ?? "") } ?? false
                    return ta != tb ? ta : a < b
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(p.table.id) · \(p.table.title)").font(.caption.weight(.semibold)).foregroundStyle(pal.text2)
                    ScrollView(.horizontal, showsIndicators: false) {
                        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 6) {
                            GridRow {
                                ForEach(dims, id: \.self) { Text($0).bold() }
                                Text(p.unit.isEmpty ? "Value" : p.unit).bold().gridColumnAlignment(.trailing)
                            }
                            .foregroundStyle(pal.text2)
                            Divider().gridCellUnsizedAxes(.horizontal)
                            ForEach(Array(p.rows.prefix(300).enumerated()), id: \.offset) { _, r in
                                GridRow {
                                    ForEach(dims, id: \.self) { d in
                                        let v = r.labels[d] ?? ""
                                        Text(isPeriod(v) ? fmtPeriod(v, lang) : v)
                                    }
                                    Text(r.value.map { fmtNum($0, digits: 3, lang) } ?? "–").monospacedDigit()
                                }
                                .foregroundStyle(pal.text)
                            }
                        }
                        .font(.caption)
                    }
                }
            }
        }
    }
}

/// The ranked hitlist each table was chosen from: rank, table, score, and which signals found it.
private struct Shortlist: View {
    @Environment(\.pal) private var pal
    let parts: [AnswerPart]
    let lang: Lang

    var body: some View {
        let w = Words(lang: lang)
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(parts.filter { !($0.ranking ?? []).isEmpty }.enumerated()), id: \.offset) { _, p in
                let ranking = p.ranking ?? []
                let top = max(ranking.map(\.score).max() ?? 1, 1e-9)
                VStack(spacing: 4) {
                    ForEach(Array(ranking.enumerated()), id: \.offset) { i, r in
                        let picked = r.id == p.table.id
                        HStack(alignment: .top, spacing: 10) {
                            Text("\(i + 1)").font(.caption.monospacedDigit()).foregroundStyle(pal.text3).frame(width: 18, alignment: .trailing)
                            VStack(alignment: .leading, spacing: 5) {
                                (Text(r.id).bold() + Text("  " + r.title)).font(.caption).foregroundStyle(pal.text)
                                Flow(spacing: 4) {
                                    ForEach(r.signals, id: \.self) { s in
                                        Text(w.signal(s)).font(.caption2).foregroundStyle(pal.text2)
                                            .padding(.horizontal, 6).padding(.vertical, 2)
                                            .background(pal.text.opacity(0.06), in: Capsule())
                                    }
                                    if picked {
                                        Text("✓ \(w.chosen)").font(.caption2.bold()).foregroundStyle(pal.accent)
                                    }
                                }
                                GeometryReader { g in
                                    Capsule().fill(pal.text.opacity(0.06))
                                        .overlay(alignment: .leading) {
                                            Capsule().fill(picked ? pal.accent : pal.accent2.opacity(0.55))
                                                .frame(width: max(4, g.size.width * r.score / top))
                                        }
                                }
                                .frame(height: 4)
                            }
                        }
                        .padding(8)
                        .background(picked ? pal.soft : .clear, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                }
            }
        }
    }
}

/// Each pipeline step with its time as a bar and what it did.
private struct StepsTimeline: View {
    @Environment(\.pal) private var pal
    let a: Answer
    let lang: Lang

    var body: some View {
        let top = Double(max(a.steps.map(\.ms).max() ?? 1, 1))
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(a.steps.enumerated()), id: \.offset) { _, s in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(s.name).font(.caption.monospaced()).foregroundStyle(pal.text).frame(width: 64, alignment: .leading)
                        GeometryReader { g in
                            Capsule().fill(pal.accent.opacity(0.7))
                                .frame(width: max(3, g.size.width * Double(s.ms) / top))
                        }
                        .frame(height: 6)
                        Text(fmtMs(s.ms)).font(.caption2.monospacedDigit()).foregroundStyle(pal.text3).frame(width: 52, alignment: .trailing)
                    }
                    if !s.detail.isEmpty {
                        Text(prettyPeriods(s.detail, lang)).font(.caption2).foregroundStyle(pal.text3)
                            .lineLimit(4).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Text(a.model).font(.caption2).foregroundStyle(pal.text3)
        }
    }
}

/// Two more questions on the same subject and one on another, as the website picks them.
func followupPicks(_ a: Answer) -> [String] {
    let isl = a.language == "is"
    let topics = Set(a.parts.compactMap { p in TOPIC_OF.first { p.table.id.range(of: $0.0, options: .regularExpression) != nil }?.1 })
    let text = { (q: PoolQ) in isl ? q.isl : q.en }
    let same = POOL.filter { topics.contains($0.topic) && text($0) != a.question }.shuffled().prefix(2)
    let other = POOL.filter { !topics.contains($0.topic) }.shuffled().prefix(1)
    return (same + other).map(text)
}

/// The answer a sentence at a time: the rest is laid out but clear, so nothing moves as each one fades in.
private struct SentenceText: View {
    let text: String
    let color: Color
    @State private var visible = 0
    @Environment(\.accessibilityReduceMotion) private var still

    private var sentences: [String] {   // split after . ! ? before a capital, so 5,9 and "jan. 2016" stay whole
        text.replacingOccurrences(of: #"(?<=[.!?])\s+(?=[A-ZÁÐÉÍÓÚÝÞÆÖ])"#, with: "\u{1}", options: .regularExpression)
            .components(separatedBy: "\u{1}")
    }

    var body: some View {
        let parts = sentences
        parts.enumerated().reduce(Text("")) { acc, e in
            acc + Text(e.element + (e.offset < parts.count - 1 ? " " : ""))
                .foregroundColor(e.offset < visible ? color : .clear)
        }
        .task(id: text) {
            if still { visible = parts.count; return }
            for i in 1...max(parts.count, 1) {
                withAnimation(.easeOut(duration: 0.45)) { visible = i }
                try? await Task.sleep(for: .milliseconds(220))
            }
        }
    }
}

/// One source: "Heimild · Hagstofa Íslands", the table's title in the answer's language, its id and latest period.
private struct SourceCard: View {
    @Environment(\.pal) private var pal
    let s: TableRef
    let lang: Lang
    var body: some View {
        let card = VStack(alignment: .leading, spacing: 3) {
            Text(lang == .is ? "HEIMILD · HAGSTOFA ÍSLANDS" : "SOURCE · STATISTICS ICELAND")
                .font(.caption2.weight(.semibold)).tracking(0.6).foregroundStyle(pal.text3)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(s.title).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text).multilineTextAlignment(.leading)
                Image(systemName: "arrow.up.right").font(.caption.weight(.semibold)).foregroundStyle(pal.accent)
            }
            Text([s.id, s.updated.map { "\(lang == .is ? "gögn til" : "data to") \(fmtPeriod($0, lang))" }].compactMap { $0 }.joined(separator: " · "))
                .font(.caption).monospacedDigit().foregroundStyle(pal.text3)
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(pal.text.opacity(0.04), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        if let url = URL(string: s.url) { Link(destination: url) { card } } else { card }
    }
}

/// Shrinks a little while pressed.
private struct Pressable: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.15), value: configuration.isPressed)
    }
}

/// A follow-up row that pops in a moment after the one before; only its small kind label carries colour.
private struct SuggestionPill: View {
    @Environment(\.pal) private var pal
    let s: Suggestion
    let lang: Lang
    let delay: Double
    let action: () -> Void
    @State private var shown = false

    private var label: String {
        let en = ["compare": "Compare", "related": "Related", "deeper": "Dig deeper", "wider": "Bigger picture"]
        let isl = ["compare": "Samanburður", "related": "Skylt efni", "deeper": "Nánar", "wider": "Víðara samhengi"]
        return (lang == .is ? isl : en)[s.kind] ?? s.kind
    }
    private var tint: Color {
        switch s.kind {
        case "related": return pal.series2
        case "deeper": return pal.series3
        case "wider": return pal.text2
        default: return pal.accent
        }
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(.caption2.weight(.semibold)).foregroundStyle(tint)
                    Text(s.question).font(.subheadline).foregroundStyle(pal.text).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 4)
                Image(systemName: "arrow.up.forward").font(.caption.weight(.semibold)).foregroundStyle(pal.text3)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(pal.text.opacity(0.04), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(Pressable())
        .opacity(shown ? 1 : 0)
        .scaleEffect(shown ? 1 : 0.85)
        .offset(y: shown ? 0 : 10)
        .onAppear {
            withAnimation(.spring(duration: 0.55, bounce: 0.35).delay(delay)) { shown = true }
        }
        .sensoryFeedback(.selection, trigger: shown)
    }
}

/// Shimmering placeholders while the follow-ups are written.
private struct GhostPills: View {
    @Environment(\.pal) private var pal
    @State private var phase = false
    var body: some View {
        Flow(spacing: 8) {
            ForEach([150.0, 200.0, 130.0, 170.0], id: \.self) { w in
                Capsule()
                    .fill(LinearGradient(colors: [pal.card, pal.soft, pal.card], startPoint: phase ? .trailing : .leading,
                                         endPoint: phase ? .leading : .trailing))
                    .overlay(Capsule().stroke(pal.text.opacity(0.08)))
                    .frame(width: w, height: 36)
            }
        }
        .onAppear { withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: true)) { phase = true } }
        .transition(.opacity)
    }
}
