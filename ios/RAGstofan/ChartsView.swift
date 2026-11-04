// Answer charts with Swift Charts: the same eight views as the website, chosen by the shape of the data.
import SwiftUI
import Charts

func isPeriod(_ x: String) -> Bool { x.range(of: #"^\d{4}([MQ]\d+)?$"#, options: .regularExpression) != nil }
func isTotal(_ x: String) -> Bool {
    x.trimmingCharacters(in: .whitespaces).range(of: #"^(total|alls|samtals|all)\b"#, options: [.regularExpression, .caseInsensitive]) != nil
}

/// Which views suit this answer, best first. The server says line or bar; the data and the calculation decide the rest.
func viewsFor(_ c: ChartSpec, _ comp: Computation?, compare: Bool = false) -> [String] {
    if compare { return ["line"] }   // rebased series: stacking or summing them means nothing
    // the server read the question and chose a view: that first, then the others the data supports
    let own = ([c.view ?? ""] + (c.views ?? [])).filter { !$0.isEmpty }
    if !own.isEmpty {
        let lines = c.type == "line" && Set(c.data.compactMap(\.series)).count > 1
        return Array(NSOrderedSet(array: own + (lines && c.unit != "%" ? ["index"] : []))) as? [String] ?? own
    }
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
    var compare = false
    private let views: [String]   // decided once: it scans every point
    @State private var view: String

    init(chart: ChartSpec, comp: Computation?, lang: Lang, compare: Bool = false) {
        self.chart = chart
        self.comp = comp
        self.lang = lang
        self.compare = compare
        views = viewsFor(chart, comp, compare: compare)
        _view = State(initialValue: views.first ?? "bars")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(chart.title).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text2)
                .fixedSize(horizontal: false, vertical: true)
            if views.count > 1 { Switcher(views: views, selection: $view, lang: lang) }
            Group {
                if view == "map" {
                    MunicipalityMap(chart: chart, lang: lang)
                } else if view == "index" {   // each line set to 100 at the first period they share: growth, side by side
                    ChartCanvas(chart: rebased(chart), comp: nil, lang: lang, view: "line", compare: true)
                } else {
                    ChartCanvas(chart: chart, comp: comp, lang: lang, view: view, compare: compare)
                }
            }
                .id(view)
                .transition(.asymmetric(insertion: .opacity.combined(with: .scale(scale: 0.98, anchor: .bottom)),
                                        removal: .opacity))
        }
        .padding(14)
        .background(pal.text.opacity(0.025), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

/// The chart views as a capsule switcher; the highlight slides to the one picked.
private struct Switcher: View {
    @Environment(\.pal) private var pal
    let views: [String]
    @Binding var selection: String
    let lang: Lang
    @Namespace private var ns

    var body: some View {
        HStack(spacing: 2) {
            ForEach(views, id: \.self) { v in
                Text(Words(lang: lang).viewName(v))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(selection == v ? pal.text : pal.text3)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background {
                        if selection == v { Capsule().fill(pal.text.opacity(0.12)).matchedGeometryEffect(id: "on", in: ns) }
                    }
                    .contentShape(Capsule())
                    .onTapGesture { withAnimation(.spring(duration: 0.4, bounce: 0.25)) { selection = v } }
            }
        }
        .padding(2)
        .background(pal.text.opacity(0.04), in: Capsule())
        .sensoryFeedback(.selection, trigger: selection)
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
    var compare = false   // series from different tables, each set to 100 at the start
    @State private var selected: String?
    @State private var grow = 0.0   // 0 → 1 as the chart draws itself in
    @State private var hidden: Set<String> = []   // series switched off in the legend

    // Everything the chart draws from, worked out once when the chart is made. These were computed properties, and
    // rise() read yDomain, which rebuilt and sorted every point: hundreds of rebuilds per frame while a long series
    // drew itself in, scrolled or was scrubbed.
    private let pts: [Pt]
    private let series: [String]        // in the order they first appear; one entry ("") for a single line
    private let labels: [String]
    private let byLabel: [String: [Pt]]
    private let yearsOnly: Bool          // many months: the axis names years only, at Januaries
    private let ticks: [String]          // at most five x labels, evenly spread, so they never collide
    private let yDomain: ClosedRange<Double>
    private let stackDomain: ClosedRange<Double>
    private let maxY: Double

    init(chart: ChartSpec, comp: Computation?, lang: Lang, view: String, compare: Bool = false) {
        self.chart = chart; self.comp = comp; self.lang = lang; self.view = view; self.compare = compare
        var d = chart.data.enumerated().map { i, p in Pt(id: i, x: p.x, label: fmtPeriod(p.x, lang), y: p.y, series: p.series ?? "") }
        if d.allSatisfy({ isPeriod($0.x) }) { d.sort { ($0.x, $0.id) < ($1.x, $1.id) } }
        pts = d
        var seenS: [String] = [], seenSet = Set<String>()
        var seenL: [String] = [], labelSet = Set<String>()
        var by: [String: [Pt]] = [:]
        for p in d {
            if seenSet.insert(p.series).inserted { seenS.append(p.series) }
            if labelSet.insert(p.label).inserted { seenL.append(p.label) }
            by[p.label, default: []].append(p)
        }
        series = seenS.isEmpty ? [""] : Array(seenS.prefix(7))
        labels = seenL
        byLabel = by
        let monthly = seenL.count > 24 && d.allSatisfy { $0.x.count == 7 && $0.x.dropFirst(4).first == "M" }
        yearsOnly = monthly
        let candidates = monthly ? seenL.filter { by[$0]?.first?.x.hasSuffix("M01") == true } : seenL
        let step = max(1, Int((Double(candidates.count) / 5).rounded(.up)))
        ticks = stride(from: 0, to: candidates.count, by: step).map { candidates[$0] }
        let ys = d.map(\.y)
        let lo = ys.min() ?? 0, hi = ys.max() ?? 1
        let pad = hi > lo ? (hi - lo) * 0.12 : max(abs(hi) * 0.05, 1)
        yDomain = (lo - pad)...(hi + pad)
        let top = by.values.map { $0.reduce(0) { $0 + max($1.y, 0) } }.max() ?? 1
        stackDomain = 0...max(top * 1.05, 1)
        maxY = hi
    }

    private var multi: Bool { series.count > 1 }
    private func colors(_ n: Int) -> [Color] { Array((pal.series + [pal.text.opacity(0.25)]).prefix(max(n, 1))) }
    private func color(_ s: String) -> Color { colors(series.count)[series.firstIndex(of: s) ?? 0] }
    /// Values while drawing in: lines rise from the floor of the chart, bars grow from zero.
    private func rise(_ y: Double) -> Double { yDomain.lowerBound + (y - yDomain.lowerBound) * grow }
    private func scaled(_ y: Double) -> Double { y * grow }

    private func val(_ v: Double) -> String {
        let pct = chart.unit == "%"
        let n = fmtNum(v, percent: pct && v.rounded() != v, lang)
        return pct ? n + "%" : chart.unit.isEmpty ? n : "\(n) \(chart.unit)"
    }
    private func compact(_ v: Double) -> String {
        v.formatted(.number.notation(.compactName).precision(.significantDigits(1...3)).locale(Locale(identifier: lang == .is ? "is_IS" : "en_US")))
    }
    private var barFill: LinearGradient {
        LinearGradient(colors: [pal.series1, pal.series1.opacity(0.7)], startPoint: .top, endPoint: .bottom)
    }

    var body: some View {
        Group {
            switch view {
            case "donut", "waffle": shareView
            case "heatmap": heatmap
            case "slope": slope
            case "lollipop": lollipop
            case "line", "stacked": lineChart
            default: bars
            }
        }
        .sensoryFeedback(.selection, trigger: selected)
        .onAppear { withAnimation(.spring(duration: 1.0, bounce: 0.12).delay(0.1)) { grow = 1 } }
    }

    // MARK: line and stacked area

    /// Several series: the legend is a row of switches. Tap one to hide that series, tap again to bring it back;
    /// the last one showing cannot be hidden. Replaces the chart's own legend, which cannot be tapped.
    private var legendRow: some View {
        Flow(spacing: 6) {
            ForEach(series, id: \.self) { s in
                let off = hidden.contains(s)
                Button {
                    if off { hidden.remove(s) } else if hidden.count < series.count - 1 { hidden.insert(s) }
                } label: {
                    HStack(spacing: 5) {
                        Circle().fill(color(s)).frame(width: 8, height: 8).opacity(off ? 0.35 : 1)
                        Text(s).font(.caption).lineLimit(1).strikethrough(off, color: pal.text3)
                            .foregroundStyle(off ? pal.text3 : pal.text2)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Capsule().fill(pal.text.opacity(off ? 0.03 : 0.06)))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(off ? "Show \(s)" : "Hide \(s)")
            }
        }
    }

    private var lineChart: some View {
        let stacked = view == "stacked", lo = yDomain.lowerBound
        let shown = pts.filter { !hidden.contains($0.series) }
        let last = shown.last
        let chart = Chart {
            ForEach(shown) { p in
                if stacked {
                    AreaMark(x: .value("x", p.label), y: .value("y", scaled(p.y)), stacking: .standard)
                        .foregroundStyle(by: .value("s", p.series))
                        .interpolationMethod(.monotone)
                        .opacity(0.85)
                } else if multi {
                    LineMark(x: .value("x", p.label), y: .value("y", rise(p.y)))
                        .foregroundStyle(by: .value("s", p.series))
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                } else {
                    AreaMark(x: .value("x", p.label), yStart: .value("lo", lo), yEnd: .value("y", rise(p.y)))
                        .foregroundStyle(LinearGradient(colors: [pal.series1.opacity(0.32), pal.series1.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                    LineMark(x: .value("x", p.label), y: .value("y", rise(p.y)))
                        .foregroundStyle(pal.series1)
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                }
            }
            // the latest value, labelled, as on the website
            if !stacked && !multi, let last {
                PointMark(x: .value("x", last.label), y: .value("y", rise(last.y)))
                    .foregroundStyle(pal.series1)
                    .symbolSize(70)
                    .annotation(position: .top, alignment: .trailing, spacing: 6) {
                        Text(val(last.y)).font(.caption2.weight(.semibold)).monospacedDigit().foregroundStyle(pal.text)
                            .opacity(grow)
                    }
                PointMark(x: .value("x", last.label), y: .value("y", rise(last.y)))
                    .foregroundStyle(pal.card)
                    .symbolSize(20)
            }
            // several lines: each named at its end with its last value, so nobody matches colours to a legend
            if multi && !stacked && series.count <= 4 {
                ForEach(series.filter { !hidden.contains($0) }, id: \.self) { s in
                    if let end = shown.last(where: { $0.series == s }) {
                        PointMark(x: .value("x", end.label), y: .value("y", rise(end.y)))
                            .foregroundStyle(color(s)).symbolSize(36)
                            .annotation(position: .top, alignment: .trailing, spacing: 4) {
                                Text("\(s) \(val(end.y))").font(.caption2.weight(.semibold)).monospacedDigit()
                                    .foregroundStyle(color(s)).opacity(grow).lineLimit(1)
                            }
                    }
                }
            }
            // a comparison set to 100 at the start: the starting line, faint, so above and below read at a glance
            if compare && chart.unit != "%" {
                RuleMark(y: .value("start", rise(100)))
                    .foregroundStyle(pal.text3.opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
            }
            if let s = selected { crosshair(s) }
        }
        .chartForegroundStyleScale(domain: series, range: colors(series.count))
        .chartYScale(domain: stacked ? stackDomain : yDomain)
        .modifier(Axes(ticks: ticks, pal: pal, compact: compact, yearsOnly: yearsOnly))
        .chartLegend(.hidden)
        .chartXSelection(value: $selected)
        .frame(height: 240)
        return VStack(alignment: .leading, spacing: 8) {
            if multi { legendRow }
            chart
        }
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
            ForEach(byLabel[label] ?? []) { p in
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
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
    }

    // MARK: bars and lollipops

    @ViewBuilder
    private var bars: some View {
        if pts.allSatisfy({ isPeriod($0.x) }) {
            Chart {
                ForEach(pts) { p in
                    if multi {
                        BarMark(x: .value("x", p.label), y: .value("y", scaled(p.y)), width: .ratio(0.72))
                            .foregroundStyle(by: .value("s", p.series))
                            .position(by: .value("s", p.series))
                            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                            .opacity(selected == nil || selected == p.label ? 1 : 0.4)
                    } else {
                        BarMark(x: .value("x", p.label), y: .value("y", scaled(p.y)), width: .ratio(0.72))
                            .foregroundStyle(barFill)
                            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 5, topTrailingRadius: 5))
                            .opacity(selected == nil || selected == p.label ? 1 : 0.4)
                    }
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
                    if multi {
                        BarMark(x: .value("y", scaled(p.y)), y: .value("x", p.label), height: .ratio(0.7))
                            .foregroundStyle(by: .value("s", p.series))
                            .position(by: .value("s", p.series))
                            .clipShape(UnevenRoundedRectangle(bottomTrailingRadius: 4, topTrailingRadius: 4))
                    } else {
                        BarMark(x: .value("y", scaled(p.y)), y: .value("x", p.label), height: .ratio(0.7))
                            .foregroundStyle(LinearGradient(colors: [pal.series1.opacity(0.7), pal.series1], startPoint: .leading, endPoint: .trailing))
                            .clipShape(UnevenRoundedRectangle(bottomTrailingRadius: 5, topTrailingRadius: 5))
                            .annotation(position: .trailing, spacing: 4) {
                                Text(val(p.y)).font(.caption2).monospacedDigit().foregroundStyle(pal.text2).opacity(grow)
                            }
                    }
                }
            }
            .chartForegroundStyleScale(domain: series, range: colors(series.count))
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) { v in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [2, 3])).foregroundStyle(pal.grid)
                    AxisValueLabel { if let d = v.as(Double.self) { Text(compact(d)) } }
                }
            }
            .chartYAxis { AxisMarks { AxisValueLabel().font(.caption2).foregroundStyle(pal.text2) } }
            .chartXScale(domain: 0...max(maxY * 1.02, 1), range: .plotDimension(endPadding: multi ? 0 : 56))
            .chartLegend(multi ? .visible : .hidden)
            .chartLegend(position: .top, alignment: .leading)
            .frame(height: CGFloat(max(150, labels.count * (multi ? 22 * series.count : 30) + 30)))
        }
    }

    private var lollipop: some View {
        Chart {
            ForEach(pts) { p in
                RuleMark(xStart: .value("0", 0), xEnd: .value("y", scaled(p.y)), y: .value("x", p.label))
                    .foregroundStyle(pal.series1.opacity(0.45))
                    .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                PointMark(x: .value("y", scaled(p.y)), y: .value("x", p.label))
                    .foregroundStyle(pal.series1)
                    .symbolSize(110)
                    .annotation(position: .trailing, spacing: 6) {
                        Text(val(p.y)).font(.caption2).monospacedDigit().foregroundStyle(pal.text2).opacity(grow)
                    }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { v in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [2, 3])).foregroundStyle(pal.grid)
                AxisValueLabel { if let d = v.as(Double.self) { Text(compact(d)) } }
            }
        }
        .chartYAxis { AxisMarks { AxisValueLabel().font(.caption2).foregroundStyle(pal.text2) } }
        .chartXScale(domain: 0...max(maxY * 1.02, 1), range: .plotDimension(endPadding: 56))
        .frame(height: CGFloat(max(150, labels.count * 32 + 30)))
    }

    // MARK: slope

    private var slope: some View {
        Chart {
            ForEach(pts) { p in
                LineMark(x: .value("x", p.label), y: .value("y", rise(p.y)))
                    .foregroundStyle(by: .value("s", p.series))
                    .lineStyle(StrokeStyle(lineWidth: 3, lineCap: .round))
                PointMark(x: .value("x", p.label), y: .value("y", rise(p.y)))
                    .foregroundStyle(by: .value("s", p.series))
                    .symbolSize(130)
                    .annotation(position: .top, spacing: 8) {
                        Text(val(p.y)).font(.caption.weight(.semibold)).monospacedDigit().foregroundStyle(pal.text).opacity(grow)
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
                            .fill(i < n && grow > 0 ? pal.series1 : pal.text.opacity(0.08))
                            .aspectRatio(1, contentMode: .fit)
                            .scaleEffect(i < n && grow > 0 ? 1 : 0.85)
                            .animation(.spring(duration: 0.4, bounce: 0.4).delay(Double(i) * 0.008), value: grow > 0)
                    }
                }
                .frame(maxWidth: 220)
                Text("\(fmtNum(first, percent: true, lang))% · \(sl.first?.key ?? "")").font(.subheadline.weight(.medium)).foregroundStyle(pal.text)
            }
            .frame(maxWidth: .infinity)
        } else {
            // the slices sweep round as the chart draws in; an empty slice holds the rest of the circle meanwhile
            Chart {
                ForEach(sl) { s in
                    SectorMark(angle: .value("v", s.value * grow), innerRadius: .ratio(0.62), angularInset: 1.5)
                        .cornerRadius(5)
                        .foregroundStyle(by: .value("k", s.key))
                }
                SectorMark(angle: .value("v", total * (1 - grow)), innerRadius: .ratio(0.62))
                    .foregroundStyle(.clear)
            }
            .chartForegroundStyleScale(domain: sl.map(\.key), range: colors(sl.count))
            .chartLegend(position: .bottom, alignment: .center, spacing: 12)
            .chartBackground { proxy in
                GeometryReader { g in
                    if let f = proxy.plotFrame {
                        let r = g[f]
                        VStack(spacing: 0) {
                            Text("\(fmtNum(first, percent: true, lang))%").font(.system(size: 26, weight: .semibold, design: .rounded))
                                .monospacedDigit()
                            Text(sl.first?.key ?? "").font(.caption2).foregroundStyle(pal.text3).lineLimit(1)
                        }
                        .frame(width: r.width * 0.55)
                        .position(x: r.midX, y: r.midY)
                        .opacity(grow)
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
            RectangleMark(x: .value("col", c.col), y: .value("year", c.row), width: .ratio(0.9), height: .ratio(0.84))
                .foregroundStyle(by: .value("v", c.value))
                .cornerRadius(4)
                .opacity(grow)
        }
        .chartForegroundStyleScale(range: Gradient(colors: [pal.series1.opacity(0.08), pal.series1.opacity(0.55), pal.series1]))
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
    var yearsOnly = false
    func body(content: Content) -> some View {
        let tickSet = Set(ticks)
        return content
            .chartXAxis {
                // a category axis takes no explicit tick list: every category gets a mark, only the evenly spread
                // ticks get text, looked up in a set
                AxisMarks { v in
                    AxisValueLabel(collisionResolution: .disabled) {
                        let s = v.as(String.self) ?? ""
                        Text(tickSet.contains(s) ? (yearsOnly ? String(s.suffix(4)) : s) : "").font(.caption2).foregroundStyle(pal.text3)
                    }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { v in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [2, 3])).foregroundStyle(pal.grid)
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


// MARK: - the server's choices: a display question's pictures, growth from 100, and the map

/// The picture beside a "how should I show this?" option.
func displayIcon(_ display: String) -> String {
    ["bars": "chart.bar.fill", "line": "chart.xyaxis.line", "map": "map.fill", "ranking": "list.number",
     "stacked": "square.stack.3d.up.fill", "heatmap": "square.grid.3x3.fill", "number": "number"][display] ?? "chart.bar"
}

/// Each series set to 100 at the first period all of them have, so a town of 20,000 and a city of 140,000 compare.
func rebased(_ c: ChartSpec) -> ChartSpec {
    var by: [String: [String: Double]] = [:]
    for p in c.data { by[p.series ?? "", default: [:]][p.x] = p.y }
    let keySets = by.values.map { Set($0.keys) }
    let shared = keySets.dropFirst().reduce(keySets.first ?? []) { acc, keys in acc.intersection(keys) }
    guard let base = shared.sorted().first else { return c }
    let data = c.data.compactMap { p -> ChartPoint? in
        guard p.x >= base, let b = by[p.series ?? ""]?[base], b != 0 else { return nil }
        return ChartPoint(x: p.x, y: (p.y / b * 1000).rounded() / 10, series: p.series)
    }
    return ChartSpec(type: "line", title: c.title, xLabel: c.xLabel, yLabel: c.yLabel, unit: "", data: data)
}

/// Iceland's municipalities, as the server serves them (frontend/municipalities.geo.json: the official IS 50V
/// outlines from gis.lmi.is, Náttúrufræðistofnun / Landmælingar Íslands, CC BY 4.0), loaded once per launch.
actor MunicipalityShapes {
    static let shared = MunicipalityShapes()
    struct Shape { let number: String; let name: String; let rings: [[CGPoint]] }   // x: longitude, y: Mercator latitude
    private var cache: [Shape]?

    func load() async -> [Shape] {
        if let cache { return cache }
        let server = UserDefaults.standard.string(forKey: "server") ?? ""
        guard let url = URL(string: server)?.appendingPathComponent("municipalities.geo.json"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let features = json["features"] as? [[String: Any]] else { return [] }
        let merc = { (lat: Double) in log(tan(.pi / 4 + lat * .pi / 360)) * 180 / .pi }
        let shapes: [Shape] = features.compactMap { f in
            guard let number = f["id"] as? String, let props = f["properties"] as? [String: Any],
                  let geom = f["geometry"] as? [String: Any], let polys = geom["coordinates"] as? [[[[Double]]]] else { return nil }
            let rings = polys.flatMap { $0 }.map { ring in ring.map { CGPoint(x: $0[0], y: merc($0[1])) } }
            return Shape(number: number, name: props["name"] as? String ?? number, rings: rings)
        }
        cache = shapes
        return shapes
    }
}

/// One shade per municipality, darker for more, on a log scale when the largest is many times the smallest
/// (Reykjavík against a village); municipalities without a figure stay pale. Pinch to zoom, drag to pan once zoomed,
/// double-tap to zoom in where tapped, the buttons for in, out and the whole country; tap a municipality for its
/// figure. An answer about a few neighbours (the capital area) opens zoomed in on them.
struct MunicipalityMap: View {
    @Environment(\.pal) private var pal
    let chart: ChartSpec
    let lang: Lang
    @State private var shapes: [MunicipalityShapes.Shape] = []
    @State private var picked: String?
    @State private var zoom: CGFloat = 1          // settled zoom
    @State private var pan: CGSize = .zero        // settled pan, in view points
    @GestureState private var pinch: CGFloat = 1  // the pinch in progress
    @GestureState private var drag: CGSize = .zero
    @State private var focused = false            // the opening zoom on the answer's municipalities is applied once

    private static let maxZoom: CGFloat = 14

    private var values: [String: (String, Double)] {
        Dictionary(chart.data.compactMap { p in p.code.map { ($0, (p.x, p.y)) } }, uniquingKeysWith: { a, _ in a })
    }

    /// Where the map's coordinates fall in a view of this size, before zooming: Iceland fitted and centred.
    private struct Frame {
        let minX, maxY, scale, offX, offY: CGFloat
        func place(_ p: CGPoint) -> CGPoint { CGPoint(x: offX + (p.x - minX) * scale, y: offY + (maxY - p.y) * scale) }
    }

    private func frame(_ size: CGSize) -> Frame {
        let all = shapes.flatMap { $0.rings.flatMap { $0 } }
        let minX = all.map(\.x).min() ?? 0, maxX = all.map(\.x).max() ?? 1
        let minY = all.map(\.y).min() ?? 0, maxY = all.map(\.y).max() ?? 1
        let scale = min(size.width / max(maxX - minX, 0.001), size.height / max(maxY - minY, 0.001))
        return Frame(minX: minX, maxY: maxY, scale: scale,
                     offX: (size.width - (maxX - minX) * scale) / 2, offY: (size.height - (maxY - minY) * scale) / 2)
    }

    private func clamp(_ z: CGFloat) -> CGFloat { min(max(z, 1), Self.maxZoom) }

    /// The zoom and pan in effect, gestures in progress included; at the whole-country zoom the map sits centred.
    private var k: CGFloat { clamp(zoom * pinch) }
    private var shift: CGSize { k <= 1.001 ? .zero : CGSize(width: pan.width + drag.width, height: pan.height + drag.height) }

    /// A point of the unzoomed map to the view, through the zoom about the view's centre and the pan.
    private func transform(_ size: CGSize) -> CGAffineTransform {
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        return CGAffineTransform(translationX: c.x + shift.width, y: c.y + shift.height).scaledBy(x: k, y: k)
            .translatedBy(x: -c.x, y: -c.y)
    }

    /// Zoom to fit the answer's municipalities when they cover a small part of the country (a few neighbours).
    private func focus(_ size: CGSize) {
        guard !focused, !shapes.isEmpty, size.width > 0 else { return }
        focused = true
        let f = frame(size), vals = values
        let pts = shapes.filter { vals[$0.number] != nil }.flatMap { $0.rings.flatMap { $0 } }.map(f.place)
        guard let minX = pts.map(\.x).min(), let maxX = pts.map(\.x).max(),
              let minY = pts.map(\.y).min(), let maxY = pts.map(\.y).max() else { return }
        let w = max(maxX - minX, 1), h = max(maxY - minY, 1)
        let z = clamp(min(size.width / (w * 1.6), size.height / (h * 1.6)))
        guard z > 2 else { return }   // the answer covers much of the country: the whole map is the right view
        let c = CGPoint(x: size.width / 2, y: size.height / 2), mid = CGPoint(x: (minX + maxX) / 2, y: (minY + maxY) / 2)
        zoom = z
        pan = CGSize(width: (c.x - mid.x) * z, height: (c.y - mid.y) * z)
    }

    private func setZoom(_ z: CGFloat, around point: CGPoint? = nil, in size: CGSize) {
        let new = clamp(z)
        withAnimation(.spring(duration: 0.35, bounce: 0.15)) {
            if new <= 1.001 { zoom = 1; pan = .zero; return }
            if let point {   // keep the tapped place under the finger, then bring it toward the centre
                let c = CGPoint(x: size.width / 2, y: size.height / 2)
                let mapPoint = point.applying(transform(size).inverted())
                pan = CGSize(width: (c.x - mapPoint.x) * new, height: (c.y - mapPoint.y) * new)
            } else {
                pan = CGSize(width: pan.width * new / max(zoom, 1), height: pan.height * new / max(zoom, 1))
            }
            zoom = new
        }
    }

    var body: some View {
        let vals = values
        let ys = vals.values.map(\.1).filter { $0 > 0 }
        let lo = ys.min() ?? 1, hi = ys.max() ?? 1
        let useLog = lo > 0 && hi / lo > 50
        let strength = { (y: Double) -> Double in
            guard hi > lo else { return 1 }
            return useLog ? (log(max(y, lo)) - log(lo)) / (log(hi) - log(lo)) : (y - lo) / (hi - lo)
        }
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                let size = geo.size, f = frame(size), t = transform(size)
                let paths: [(String, Path)] = shapes.map { s in
                    var path = Path()
                    for ring in s.rings where ring.count > 2 {
                        path.move(to: f.place(ring[0]))
                        for p in ring.dropFirst() { path.addLine(to: f.place(p)) }
                        path.closeSubpath()
                    }
                    return (s.number, path)
                }
                ZStack(alignment: .topTrailing) {
                    Canvas { ctx, _ in
                        ctx.concatenate(t)
                        for (number, path) in paths {
                            let fill: Color = vals[number].map { pal.series1.opacity(0.18 + 0.82 * strength($0.1)) } ?? pal.text.opacity(0.06)
                            ctx.fill(path, with: .color(fill))
                            let on = number == picked
                            ctx.stroke(path, with: .color(on ? pal.text : pal.bg.opacity(0.9)), lineWidth: (on ? 1.6 : 0.5) / k)
                        }
                    }
                    .contentShape(Rectangle())
                    .clipped()
                    .gesture(MagnifyGesture()
                        .updating($pinch) { v, state, _ in state = v.magnification }
                        .onEnded { v in setZoom(zoom * v.magnification, in: size) })
                    // dragging pans only when zoomed in; at the whole country the page scrolls as usual
                    .simultaneousGesture(DragGesture(minimumDistance: 4)
                        .updating($drag) { v, state, _ in if k > 1.001 { state = v.translation } }
                        .onEnded { v in if k > 1.001 { pan.width += v.translation.width; pan.height += v.translation.height } },
                        including: k > 1.001 ? .all : .subviews)
                    .onTapGesture(count: 2) { at in setZoom(zoom * 2.5, around: at, in: size) }
                    .onTapGesture { at in
                        let p = at.applying(t.inverted())   // the municipality under the finger, on the unzoomed map
                        picked = paths.first { $0.1.contains(p) }?.0
                    }
                    VStack(spacing: 6) {
                        mapButton("plus") { setZoom(zoom * 2, in: size) }
                        mapButton("minus") { setZoom(zoom / 2, in: size) }
                        if k > 1.001 { mapButton("arrow.down.right.and.arrow.up.left") { setZoom(1, in: size) } }
                    }
                    .padding(6)
                }
                .onAppear { focus(size) }
                .onChange(of: shapes.count) { focus(size) }
            }
            .frame(height: 300)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(lang == .is ? "Kort af sveitarfélögum" : "Map of municipalities")
            .accessibilityHint(lang == .is ? "Klíptu til að þysja, dragðu til að færa" : "Pinch to zoom, drag to move")
            if let n = picked, let s = shapes.first(where: { $0.number == n }) {
                let v = vals[n]
                Text("\(v?.0 ?? s.name): \(v.map { fmtNum($0.1, percent: false, lang) + (chart.unit.isEmpty ? "" : " \(chart.unit)") } ?? "–")")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(pal.text)
            }
            HStack(spacing: 6) {
                Text(fmtNum(lo, percent: false, lang)).font(.caption2).foregroundStyle(pal.text3)
                LinearGradient(colors: [pal.series1.opacity(0.18), pal.series1], startPoint: .leading, endPoint: .trailing)
                    .frame(width: 120, height: 6).clipShape(Capsule())
                Text(fmtNum(hi, percent: false, lang)).font(.caption2).foregroundStyle(pal.text3)
                if useLog { Text(lang == .is ? "lograkvarði" : "log scale").font(.caption2).foregroundStyle(pal.text3) }
            }
        }
        .task { shapes = await MunicipalityShapes.shared.load() }
    }

    private func mapButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.caption.weight(.bold)).foregroundStyle(pal.text)
                .frame(width: 32, height: 32)
                .background(.ultraThinMaterial, in: Circle())
        }
        .buttonStyle(.plain)
    }
}
