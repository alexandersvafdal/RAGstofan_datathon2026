// The answer card (frontend renderAnswer): text, headline figures, charts, notes, sources, and the details behind it.
import SwiftUI

private struct Figure: Identifiable { let id: Int; let value: String; let unit: String; let label: String; let calc: String? }

struct AnswerCard: View {
    @Environment(\.pal) private var pal
    let a: Answer
    let ask: Ask
    @State private var picks: [String]

    init(a: Answer, ask: @escaping Ask) {
        self.a = a
        self.ask = ask
        _picks = State(initialValue: followupPicks(a))
    }

    private var L: Lang { a.language == "is" ? .is : .en }
    private var secs: String { String(format: "%.1f s", Double(a.totalMs) / 1000) }

    var body: some View {
        let w = Words(lang: L)
        Card {
            VStack(alignment: .leading, spacing: 18) {
                Text(prettyPeriods(a.text, L))
                    .font(.system(size: 17))
                    .lineSpacing(3)
                    .foregroundStyle(a.status == "error" ? Color.red : pal.text)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)

                if a.status == "clarify", let c = a.clarification {
                    VStack(spacing: 8) {
                        ForEach(c.options, id: \.tableId) { o in
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
                        }
                    }
                }

                let figs = figures(w)
                if !figs.isEmpty {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 12, alignment: .topLeading)], alignment: .leading, spacing: 12) {
                        ForEach(figs) { FigureView(fig: $0) }
                    }
                }

                ForEach(Array(a.parts.enumerated()), id: \.offset) { _, p in
                    if let c = p.chart, c.type != "kpi", c.data.count > 1 {
                        ChartBlock(chart: c, comp: p.computation, lang: L)
                    }
                }

                ForEach(Array(a.parts.compactMap(\.note).filter { !$0.isEmpty }.enumerated()), id: \.offset) { _, note in
                    Label { Text(prettyPeriods(note, L)) } icon: { Image(systemName: "info.circle") }
                        .font(.footnote)
                        .foregroundStyle(pal.text2)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(pal.warn, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                }

                Flow(spacing: 6) {
                    ForEach(a.sources, id: \.id) { s in
                        if let url = URL(string: s.url) {
                            Link(destination: url) { Chip(text: Text("\(w.source) ") + Text(s.id).bold(), icon: "arrow.up.right.square", strong: true) }
                        }
                    }
                    if a.textSource == "model" { Chip(text: Text(w.checked), icon: "checkmark.seal") }
                    if a.totalMs > 0 { Chip(text: Text(secs), icon: "clock") }
                }

                if !a.parts.isEmpty {
                    Disclosure(title: w.data) { DataTables(parts: a.parts, lang: L) }
                }
                if a.parts.contains(where: { !($0.ranking ?? []).isEmpty }) {
                    Disclosure(title: w.shortlist) { Shortlist(parts: a.parts, lang: L) }
                }
                if !a.steps.isEmpty {
                    Disclosure(title: "\(w.how) · \(secs) · \(a.llmCalls) \(L == .is ? "köll" : "calls")") { StepsTimeline(a: a, lang: L) }
                }

                Flow(spacing: 8) {
                    ForEach(Array(picks.enumerated()), id: \.offset) { _, q in
                        Button(q) { ask(q, nil) }.buttonStyle(PillStyle())
                    }
                }
                .padding(.top, 2)
            }
        }
    }

    /// A calculation: its result. A series: where it started and where it is now. Several lines: the latest of each.
    private func figures(_ w: Words) -> [Figure] {
        var out: [Figure] = []
        for p in a.parts {
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
                                  unit: pct ? "%" : f.unit, label: prettyPeriods(f.label, L),
                                  calc: p.computation.map { "\(w.calc): \($0.expression)" }))
            }
        }
        return out
    }
}

private struct FigureView: View {
    @Environment(\.pal) private var pal
    let fig: Figure
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(fig.value).font(.system(size: 30, weight: .semibold, design: .rounded)).monospacedDigit()
                    .foregroundStyle(pal.text).minimumScaleFactor(0.6).lineLimit(1)
                if !fig.unit.isEmpty { Text(fig.unit).font(.subheadline).foregroundStyle(pal.text3).lineLimit(1) }
            }
            Text(fig.label).font(.caption).foregroundStyle(pal.text2).fixedSize(horizontal: false, vertical: true)
            if let c = fig.calc { Text(c).font(.caption2.monospaced()).foregroundStyle(pal.text3).fixedSize(horizontal: false, vertical: true) }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(pal.soft, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
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
