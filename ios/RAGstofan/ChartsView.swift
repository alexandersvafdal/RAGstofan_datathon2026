// Answer charts with Swift Charts: the same eight views as the website, chosen by the shape of the data.
import SwiftUI
import Charts

func isPeriod(_ x: String) -> Bool { x.range(of: #"^\d{4}([MQ]\d+)?$"#, options: .regularExpression) != nil }
func isTotal(_ x: String) -> Bool {
    x.trimmingCharacters(in: .whitespaces).range(of: #"^(total|alls|samtals|all)\b"#, options: [.regularExpression, .caseInsensitive]) != nil
}

/// Which views suit this answer, best first. The server says line or bar; the data and the calculation decide the rest.
func viewsFor(_ c: ChartSpec, _ comp: Computation?) -> [String] {
    let multi = Set(c.data.compactMap(\.series)).count > 1
    let isTime = c.data.allSatisfy { isPeriod($0.x) }
    let monthly = isTime && c.data.allSatisfy { $0.x.range(of: #"M\d\d$"#, options: .regularExpression) != nil }
    let quarterly = isTime && c.data.allSatisfy { $0.x.range(of: #"Q\d$"#, options: .regularExpression) != nil }
    if comp?.op == "share" && c.data.count == 2 { return ["donut", "waffle", "bars"] }
    // a slope reads left to right, so it suits two periods; two categories (men vs women) are compared as bars
    if ["difference", "pct_change"].contains(comp?.op ?? "") && c.data.count == 2 { return isTime ? ["slope", "bars"] : ["bars"] }
    if c.type == "line" {
        if multi { return ["line", "stacked", "bars"] }
        if (monthly && c.data.count >= 18) || (quarterly && c.data.count >= 8) { return ["line", "heatmap", "bars"] }
        return ["line", "bars"]
    }
    if isTime { return c.data.count == 2 ? ["slope", "bars"] : ["bars", "line"] }
    let parts = c.data.filter { !isTotal($0.x) }
    if !multi && (2...7).contains(parts.count) && parts.allSatisfy({ $0.y >= 0 }) { return ["bars", "donut", "lollipop"] }
    return multi ? ["bars"] : ["bars", "lollipop"]
}

struct ChartBlock: View {
    @Environment(\.pal) private var pal
    let chart: ChartSpec
    let comp: Computation?
    let lang: Lang
    @State private var view: String

    init(chart: ChartSpec, comp: Computation?, lang: Lang) {
        self.chart = chart
        self.comp = comp
        self.lang = lang
        _view = State(initialValue: viewsFor(chart, comp).first ?? "bars")
    }

    var body: some View {
        let views = viewsFor(chart, comp)
        VStack(alignment: .leading, spacing: 12) {
            Text(chart.title).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text2)
            if views.count > 1 {
                Picker("Chart", selection: $view.animation(.smooth)) {
                    ForEach(views, id: \.self) { Text(Words(lang: lang).viewName($0)).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            ChartCanvas(chart: chart, comp: comp, lang: lang, view: view)
                .id(view)
                .transition(.opacity)
        }
    }
}

private struct Pt: Identifiable { let id: Int; let x: String; let label: String; let y: Double; let series: String }
private struct Slice: Identifiable { let id: Int; let key: String; let value: Double }
private struct Cell: Identifiable { let id: Int; let col: String; let row: String; let value: Double }

private struct ChartCanvas: View {
    @Environment(\.pal) private var pal
    let chart: ChartSpec
    let comp: Computation?
    let lang: Lang
    let view: String
    @State private var selected: String?

    private var pts: [Pt] {
        var d = chart.data.enumerated().map { i, p in Pt(id: i, x: p.x, label: fmtPeriod(p.x, lang), y: p.y, series: p.series ?? "") }
        if d.allSatisfy({ isPeriod($0.x) }) { d.sort { ($0.x, $0.id) < ($1.x, $1.id) } }
        return d
    }
    /// Series in the order they first appear; one entry ("") when there is a single line.
    private var series: [String] {
        var seen: [String] = []
        for p in pts where !seen.contains(p.series) { seen.append(p.series) }
        return seen.isEmpty ? [""] : Array(seen.prefix(7))
    }
    private var multi: Bool { series.count > 1 }
    private func colors(_ n: Int) -> [Color] { Array((pal.series + [pal.text.opacity(0.25)]).prefix(max(n, 1))) }
    private func color(_ s: String) -> Color { colors(series.count)[series.firstIndex(of: s) ?? 0] }
    private var labels: [String] {
        var seen: [String] = []
        for p in pts where !seen.contains(p.label) { seen.append(p.label) }
        return seen
    }
    /// At most five x labels, evenly spread, so they never collide.
    private var ticks: [String] {
        let step = max(1, Int((Double(labels.count) / 5).rounded(.up)))
        return stride(from: 0, to: labels.count, by: step).map { labels[$0] }
    }
    private var yDomain: ClosedRange<Double> {
        let ys = pts.map(\.y)
        let lo = ys.min() ?? 0, hi = ys.max() ?? 1
        let pad = hi > lo ? (hi - lo) * 0.12 : max(abs(hi) * 0.05, 1)
        return (lo - pad)...(hi + pad)
    }

    /// Zero to the tallest stack.
    private var stackDomain: ClosedRange<Double> {
        let top = Dictionary(grouping: pts, by: \.label).values.map { $0.reduce(0) { $0 + max($1.y, 0) } }.max() ?? 1
        return 0...max(top * 1.05, 1)
    }

    private func val(_ v: Double) -> String {
        let pct = chart.unit == "%"
        let n = fmtNum(v, percent: pct && v.rounded() != v, lang)
        return pct ? n + "%" : chart.unit.isEmpty ? n : "\(n) \(chart.unit)"
    }
    private func compact(_ v: Double) -> String {
        v.formatted(.number.notation(.compactName).precision(.significantDigits(1...3)).locale(Locale(identifier: lang == .is ? "is_IS" : "en_US")))
    }

    var body: some View {
        switch view {
        case "donut", "waffle": shareView
        case "heatmap": heatmap
        case "slope": slope
        case "lollipop": lollipop
        case "line", "stacked": lineChart
        default: bars
        }
    }

    // MARK: line and stacked area

    private var lineChart: some View {
        let stacked = view == "stacked", lo = yDomain.lowerBound
        return Chart {
            ForEach(pts) { p in
                if stacked {
                    AreaMark(x: .value("x", p.label), y: .value("y", p.y), stacking: .standard)
                        .foregroundStyle(by: .value("s", p.series))
                        .interpolationMethod(.monotone)
                        .opacity(0.85)
                } else if multi {
                    LineMark(x: .value("x", p.label), y: .value("y", p.y))
                        .foregroundStyle(by: .value("s", p.series))
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                } else {
                    AreaMark(x: .value("x", p.label), yStart: .value("lo", lo), yEnd: .value("y", p.y))
                        .foregroundStyle(LinearGradient(colors: [pal.series1.opacity(0.28), pal.series1.opacity(0)], startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                    LineMark(x: .value("x", p.label), y: .value("y", p.y))
                        .foregroundStyle(pal.series1)
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                }
            }
            if let s = selected { crosshair(s) }
        }
        .chartForegroundStyleScale(domain: series, range: colors(series.count))
                .chartYScale(domain: stacked ? stackDomain : yDomain)
        .modifier(Axes(ticks: ticks, pal: pal, compact: compact))
        .chartLegend(multi ? .visible : .hidden)
        .chartLegend(position: .top, alignment: .leading)
        .chartXSelection(value: $selected)
        .frame(height: 240)
    }

    @ChartContentBuilder
    private func crosshair(_ s: String) -> some ChartContent {
        RuleMark(x: .value("x", s))
            .foregroundStyle(pal.text3.opacity(0.6))
            .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            .annotation(position: .top, spacing: 4, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                tooltip(s)
            }
    }

    private func tooltip(_ label: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption2).foregroundStyle(pal.text3)
            ForEach(pts.filter { $0.label == label }) { p in
                HStack(spacing: 5) {
                    if multi {
                        Circle().fill(color(p.series)).frame(width: 7, height: 7)
                        Text(p.series).font(.caption2).foregroundStyle(pal.text2).lineLimit(1)
                    }
                    Text(val(p.y)).font(.caption.weight(.semibold)).monospacedDigit().foregroundStyle(pal.text)
                }
            }
        }
        .padding(8)
        .background(pal.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
    }

    // MARK: bars and lollipops

    @ViewBuilder
    private var bars: some View {
        if pts.allSatisfy({ isPeriod($0.x) }) {
            Chart {
                ForEach(pts) { p in
                    BarMark(x: .value("x", p.label), y: .value("y", p.y), width: .ratio(0.72))
                        .foregroundStyle(by: .value("s", p.series))
                        .position(by: .value("s", p.series))
                        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                        .opacity(selected == nil || selected == p.label ? 1 : 0.45)
                }
                if let s = selected { crosshair(s) }
            }
            .chartForegroundStyleScale(domain: series, range: colors(series.count))
            .modifier(Axes(ticks: ticks, pal: pal, compact: compact))
            .chartLegend(multi ? .visible : .hidden)
            .chartLegend(position: .top, alignment: .leading)
            .chartXSelection(value: $selected)
            .frame(height: 240)
        } else {
            // categories read best as horizontal bars, labelled at the end
            Chart {
                ForEach(pts) { p in
                    BarMark(x: .value("y", p.y), y: .value("x", p.label), height: .ratio(0.7))
                        .foregroundStyle(by: .value("s", p.series))
                        .position(by: .value("s", p.series))
                        .clipShape(UnevenRoundedRectangle(bottomTrailingRadius: 4, topTrailingRadius: 4))
                        .annotation(position: .trailing, spacing: 4) {
                            if !multi { Text(val(p.y)).font(.caption2).foregroundStyle(pal.text2) }
                        }
                }
            }
            .chartForegroundStyleScale(domain: series, range: colors(series.count))
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) { v in
                    AxisGridLine().foregroundStyle(pal.grid)
                    AxisValueLabel { if let d = v.as(Double.self) { Text(compact(d)) } }
                }
            }
            .chartYAxis { AxisMarks { AxisValueLabel().font(.caption2).foregroundStyle(pal.text2) } }
            .chartXScale(range: .plotDimension(endPadding: multi ? 0 : 56))
            .chartLegend(multi ? .visible : .hidden)
            .chartLegend(position: .top, alignment: .leading)
            .frame(height: CGFloat(max(150, labels.count * (multi ? 22 * series.count : 30) + 30)))
        }
    }

    private var lollipop: some View {
        Chart {
            ForEach(pts) { p in
                RuleMark(xStart: .value("0", 0), xEnd: .value("y", p.y), y: .value("x", p.label))
                    .foregroundStyle(pal.series1.opacity(0.45))
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                PointMark(x: .value("y", p.y), y: .value("x", p.label))
                    .foregroundStyle(pal.series1)
                    .symbolSize(110)
                    .annotation(position: .trailing, spacing: 6) { Text(val(p.y)).font(.caption2).foregroundStyle(pal.text2) }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { v in
                AxisGridLine().foregroundStyle(pal.grid)
                AxisValueLabel { if let d = v.as(Double.self) { Text(compact(d)) } }
            }
        }
        .chartYAxis { AxisMarks { AxisValueLabel().font(.caption2).foregroundStyle(pal.text2) } }
        .chartXScale(range: .plotDimension(endPadding: 56))
        .frame(height: CGFloat(max(150, labels.count * 32 + 30)))
    }

    // MARK: slope

    private var slope: some View {
        Chart {
            ForEach(pts) { p in
                LineMark(x: .value("x", p.label), y: .value("y", p.y))
                    .foregroundStyle(by: .value("s", p.series))
                    .lineStyle(StrokeStyle(lineWidth: 3, lineCap: .round))
                PointMark(x: .value("x", p.label), y: .value("y", p.y))
                    .foregroundStyle(by: .value("s", p.series))
                    .symbolSize(130)
                    .annotation(position: .top, spacing: 8) {
                        Text(val(p.y)).font(.caption.weight(.semibold)).monospacedDigit().foregroundStyle(pal.text)
                    }
            }
        }
        .chartForegroundStyleScale(domain: series, range: colors(series.count))
        .chartYScale(domain: yDomain)
        .chartXScale(range: .plotDimension(padding: 44))
        .chartYAxis(.hidden)
        .chartXAxis { AxisMarks { AxisValueLabel().font(.caption).foregroundStyle(pal.text2) } }
        .chartLegend(multi ? .visible : .hidden)
        .frame(height: 220)
    }

    // MARK: donut and waffle

    /// For a share: the part and the rest of the whole. Otherwise the parts, without the total.
    private var slices: [Slice] {
        if comp?.op == "share" && pts.count == 2 {
            let s = pts.sorted { $0.y < $1.y }
            return [Slice(id: 0, key: s[0].label, value: s[0].y),
                    Slice(id: 1, key: lang == .is ? "Annað" : "The rest", value: max(0, s[1].y - s[0].y))]
        }
        return pts.filter { !isTotal($0.x) && $0.y > 0 }.enumerated().map { i, p in Slice(id: i, key: p.label, value: p.y) }
    }

    @ViewBuilder
    private var shareView: some View {
        let sl = slices
        let total = max(sl.map(\.value).reduce(0, +), 1e-9)
        let first = sl.first.map { $0.value / total * 100 } ?? 0
        if view == "waffle" {
            let n = Int(first.rounded())
            VStack(spacing: 12) {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 3), count: 10), spacing: 3) {
                    ForEach(0..<100, id: \.self) { i in
                        RoundedRectangle(cornerRadius: 3, style: .continuous)
                            .fill(i < n ? pal.series1 : pal.text.opacity(0.08))
                            .aspectRatio(1, contentMode: .fit)
                    }
                }
                .frame(maxWidth: 220)
                Text("\(fmtNum(first, percent: true, lang))% · \(sl.first?.key ?? "")").font(.subheadline.weight(.medium)).foregroundStyle(pal.text)
            }
            .frame(maxWidth: .infinity)
        } else {
            Chart(sl) { s in
                SectorMark(angle: .value("v", s.value), innerRadius: .ratio(0.62), angularInset: 1.5)
                    .cornerRadius(4)
                    .foregroundStyle(by: .value("k", s.key))
            }
            .chartForegroundStyleScale(domain: sl.map(\.key), range: colors(sl.count))
            .chartLegend(position: .bottom, alignment: .center, spacing: 12)
            .chartBackground { proxy in
                GeometryReader { g in
                    if let f = proxy.plotFrame {
                        let r = g[f]
                        VStack(spacing: 0) {
                            Text("\(fmtNum(first, percent: true, lang))%").font(.system(size: 26, weight: .semibold, design: .rounded))
                            Text(sl.first?.key ?? "").font(.caption2).foregroundStyle(pal.text3).lineLimit(1)
                        }
                        .frame(width: r.width * 0.55)
                        .position(x: r.midX, y: r.midY)
                    }
                }
            }
            .frame(height: 260)
        }
    }

    // MARK: heatmap (years down, months or quarters across)

    private var heatmap: some View {
        let cells: [Cell] = pts.enumerated().map { i, p in
            let year = String(p.x.prefix(4)), rest = String(p.x.dropFirst(5))
            let col = p.x.contains("M") ? (monthNames[lang]![(Int(rest) ?? 1) - 1]) : "Q\(rest)"
            return Cell(id: i, col: col, row: year, value: p.y)
        }
        let years = Set(cells.map(\.row)).count
        return Chart(cells) { c in
            RectangleMark(x: .value("col", c.col), y: .value("year", c.row), width: .ratio(0.92), height: .ratio(0.86))
                .foregroundStyle(by: .value("v", c.value))
                .cornerRadius(3)
        }
        .chartForegroundStyleScale(range: Gradient(colors: [pal.series1.opacity(0.1), pal.series1]))
        .chartLegend(.hidden)
        .chartXAxis { AxisMarks { AxisValueLabel().font(.system(size: 9)).foregroundStyle(pal.text3) } }
        .chartYAxis { AxisMarks(position: .leading) { AxisValueLabel().font(.caption2).foregroundStyle(pal.text2) } }
        .frame(height: CGFloat(years * 26 + 30))
    }
}

/// Shared axes for time charts: sparse x labels and compact y numbers on faint grid lines.
private struct Axes: ViewModifier {
    let ticks: [String]
    let pal: Palette
    let compact: (Double) -> String
    func body(content: Content) -> some View {
        content
            .chartXAxis {
                AxisMarks { v in
                    // every category gets a mark; only the evenly spread ticks get text
                    AxisValueLabel(collisionResolution: .disabled) {
                        let s = v.as(String.self) ?? ""
                        Text(ticks.contains(s) ? s : "").font(.caption2).foregroundStyle(pal.text3)
                    }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { v in
                    AxisGridLine().foregroundStyle(pal.grid)
                    AxisValueLabel { if let d = v.as(Double.self) { Text(compact(d)).font(.caption2).foregroundStyle(pal.text3) } }
                }
            }
    }
}

/// The dashboard tiles' sparkline.
struct MiniChart: View {
    @Environment(\.pal) private var pal
    let series: [ChartPoint]
    var body: some View {
        let ys = series.map(\.y)
        let lo = ys.min() ?? 0, hi = ys.max() ?? 1
        let pad = hi > lo ? (hi - lo) * 0.15 : 1
        Chart {
            ForEach(Array(series.enumerated()), id: \.offset) { i, p in
                AreaMark(x: .value("i", i), yStart: .value("lo", lo - pad), yEnd: .value("y", p.y))
                    .foregroundStyle(LinearGradient(colors: [pal.series1.opacity(0.25), pal.series1.opacity(0)], startPoint: .top, endPoint: .bottom))
                    .interpolationMethod(.monotone)
                LineMark(x: .value("i", i), y: .value("y", p.y))
                    .foregroundStyle(pal.series1)
                    .interpolationMethod(.monotone)
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
            }
            if let last = series.last {
                PointMark(x: .value("i", series.count - 1), y: .value("y", last.y)).foregroundStyle(pal.series1).symbolSize(30)
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: (lo - pad)...(hi + pad))
        .chartXScale(domain: 0...max(series.count - 1, 1))
    }
}
