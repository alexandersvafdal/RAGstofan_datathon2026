// The home page: greeting, floating example questions that come and go, and the latest-numbers dashboard.
import SwiftUI

struct PoolQ: Hashable { let topic: String; let en: String; let isl: String }

// Example questions (frontend POOL). topic ties them to tables for follow-up suggestions.
let POOL: [PoolQ] = [
    .init(topic: "population", en: "How many people live in Iceland?", isl: "Hve margir búa á Íslandi?"),
    .init(topic: "population", en: "How many people live in Reykjavík?", isl: "Hvað búa margir í Reykjavík?"),
    .init(topic: "population", en: "How much did Reykjavík grow from 2025 to 2026?", isl: "Hve mikið fjölgaði í Reykjavík frá 2025 til 2026?"),
    .init(topic: "population", en: "What share of Iceland's people live in Reykjavík?", isl: "Hve stórt hlutfall landsmanna býr í Reykjavík?"),
    .init(topic: "population", en: "How many people live in Kópavogur and Hafnarfjörður together?", isl: "Hve margir búa í Kópavogi og Hafnarfirði samtals?"),
    .init(topic: "population", en: "How many more men than women live in Iceland?", isl: "Hve mörgum fleiri karlar en konur búa á Íslandi?"),
    .init(topic: "population", en: "How many children were born in Iceland in 2025?", isl: "Hve mörg börn fæddust á Íslandi árið 2025?"),
    .init(topic: "population", en: "How many immigrants live in Iceland?", isl: "Hve margir innflytjendur búa á Íslandi?"),
    .init(topic: "prices", en: "What is the annual inflation rate?", isl: "Hver er verðbólgan?"),
    .init(topic: "prices", en: "How has the consumer price index changed since 2020?", isl: "Hvernig hefur vísitala neysluverðs þróast frá 2020?"),
    .init(topic: "prices", en: "How much have house prices risen in the last year?", isl: "Hve mikið hefur íbúðaverð hækkað síðasta árið?"),
    .init(topic: "wages", en: "How much have wages risen in the last 12 months?", isl: "Hve mikið hafa laun hækkað síðustu 12 mánuði?"),
    .init(topic: "labour", en: "What is the unemployment rate?", isl: "Hvert er atvinnuleysið?"),
    .init(topic: "labour", en: "How many people live in Iceland, and what is the unemployment rate?", isl: "Hve margir búa á Íslandi og hvert er atvinnuleysið?"),
    .init(topic: "tourism", en: "How many tourists came to Iceland in 2025?", isl: "Hve margir ferðamenn komu til Íslands árið 2025?"),
    .init(topic: "tourism", en: "How many overnight stays were there in registered accommodation?", isl: "Hve margar gistinætur voru á skráðum gististöðum?"),
    .init(topic: "tourism", en: "How many overnight stays were there in 2025 per inhabitant?", isl: "Hve margar gistinætur voru árið 2025 á hvern íbúa?"),
    .init(topic: "tourism", en: "How many foreign passengers came through Keflavík airport in August?", isl: "Hve margir erlendir farþegar fóru um Keflavíkurflugvöll í ágúst?"),
    .init(topic: "tourism", en: "What share of overnight stays were by foreigners in 2025?", isl: "Hve stór hluti gistinátta var vegna útlendinga árið 2025?"),
    .init(topic: "economy", en: "What was Iceland's economic growth last year?", isl: "Hver var hagvöxturinn á síðasta ári?"),
    .init(topic: "economy", en: "What was Iceland's GDP in 2025?", isl: "Hver var landsframleiðslan árið 2025?"),
    .init(topic: "economy", en: "How much fish did Iceland export last year?", isl: "Hve mikið var flutt út af sjávarafurðum á síðasta ári?"),
    .init(topic: "transport", en: "How many passenger cars were registered in Iceland in 2025?", isl: "Hve margir fólksbílar voru skráðir á Íslandi árið 2025?"),
]

let TOPIC_OF: [(String, String)] = [("^MAN", "population"), ("^VIS", "prices"), ("^LAU", "wages"), ("^VIN", "labour"),
                                    ("^SAM0[12]", "tourism"), ("^(THJ|UTA)", "economy"), ("^SAM03", "transport")]

struct HomeView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let ask: Ask
    let openSettings: () -> Void

    var body: some View {
        let w = Words(lang: model.ui)
        VStack(spacing: 26) {
            VStack(spacing: 10) {
                Text(w.eyebrow.uppercased()).font(.caption.weight(.semibold)).tracking(1.2).foregroundStyle(pal.accent)
                Text(w.hello).font(.system(size: 38, weight: .bold, design: .rounded)).foregroundStyle(pal.text)
                    .multilineTextAlignment(.center)
                Text(w.sub).font(.callout).foregroundStyle(pal.text2).multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                status
            }
            .padding(.top, 18)

            if let ind = model.indicators, !ind.indicators.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(w.today).font(.title3.weight(.semibold)).foregroundStyle(pal.text)
                        Spacer()
                        Text("\(w.asof) \(ind.dataAsOf)").font(.caption).foregroundStyle(pal.text3)
                    }
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12)], spacing: 12) {
                        ForEach(ind.indicators) { Tile(ind: $0, lang: model.ui, ask: ask) }
                    }
                }
            }

            // the headline figures first, as on Statistics Iceland's front page; then questions to try
            FloatingPills(lang: model.ui, ask: ask)
        }
    }

    private var status: some View {
        ConnectionBadge(openSettings: openSettings).padding(.top, 6)
    }
}

/// The server's state on the home page. Connecting: a breathing dot. Connected: a check that springs in with the model
/// and table count. Unreachable: a calm card that says why, counts down to the next automatic try, and can try now.
struct ConnectionBadge: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let openSettings: () -> Void
    @State private var breathe = false

    var body: some View {
        let isl = model.ui == .is
        ZStack {
            switch model.connection {
            case .connecting:
                HStack(spacing: 8) {
                    ZStack {
                        Circle().fill(pal.accent.opacity(0.3)).frame(width: 14, height: 14)
                            .scaleEffect(breathe ? 1.4 : 0.8).opacity(breathe ? 0 : 1)
                        Circle().fill(pal.accent).frame(width: 7, height: 7)
                    }
                    .onAppear { withAnimation(.easeOut(duration: 1.2).repeatForever(autoreverses: false)) { breathe = true } }
                    .onDisappear { breathe = false }
                    Text(isl ? "Tengist þjóninum…" : "Connecting to the server…")
                }
                .font(.caption).foregroundStyle(pal.text3)
                .transition(.blurReplace)
            case .connected:
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        .symbolEffect(.bounce, value: model.connection)
                    if let h = model.health {
                        Text("\(model.activeModel ?? h.model) · \(fmtNum(Double(h.tablesInCatalog), model.ui)) \(isl ? "töflur" : "tables")")
                    }
                }
                .font(.caption).foregroundStyle(pal.text3)
                .transition(.blurReplace)
            case .offline:
                VStack(spacing: 10) {
                    HStack(spacing: 10) {
                        Image(systemName: "wifi.exclamationmark").font(.title3).foregroundStyle(.orange)
                            .symbolEffect(.pulse, isActive: true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(isl ? "Næ ekki sambandi við þjóninn" : "Can't reach the server").font(.subheadline.weight(.semibold))
                                .foregroundStyle(pal.text)
                            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                                let left = max(0, Int((model.nextRetry ?? ctx.date).timeIntervalSince(ctx.date).rounded(.up)))
                                Text(left > 0 ? (isl ? "Reyni aftur eftir \(left) s · athugaðu Wi-Fi eða Tailscale"
                                                    : "Trying again in \(left) s · check Wi-Fi or Tailscale")
                                              : (isl ? "Reyni aftur…" : "Trying again…"))
                                    .contentTransition(.numericText(countsDown: true))
                                    .animation(.snappy, value: left)
                            }
                            .font(.caption).foregroundStyle(pal.text3)
                        }
                    }
                    HStack(spacing: 8) {
                        Button { Task { await model.connect() } } label: {
                            Label(isl ? "Reyna núna" : "Try now", systemImage: "arrow.clockwise")
                        }
                        .buttonStyle(PillStyle())
                        Button(action: openSettings) {
                            Label(isl ? "Þjónn" : "Server", systemImage: "server.rack")
                        }
                        .buttonStyle(PillStyle())
                    }
                }
                .padding(14)
                .background(pal.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Color.orange.opacity(0.25)))
                .transition(.scale(scale: 0.92).combined(with: .opacity))
            }
        }
        .animation(.spring(duration: 0.5, bounce: 0.25), value: model.connection)
        .sensoryFeedback(trigger: model.connection) { _, new in
            switch new { case .connected: .success; case .offline: .warning; default: nil }
        }
    }
}

/// Six example questions that bob gently; every few seconds one fades out and another takes its place.
private struct FloatingPills: View {
    let lang: Lang
    let ask: Ask
    @State private var shown: [PoolQ] = Array(POOL.shuffled().prefix(6))
    private let timer = Timer.publish(every: 3.2, on: .main, in: .common).autoconnect()

    var body: some View {
        Flow(spacing: 10, centered: true) {
            ForEach(Array(shown.enumerated()), id: \.element) { i, q in
                let text = lang == .is ? q.isl : q.en
                Button(text) { ask(text, nil) }
                    .buttonStyle(PillStyle())
                    .modifier(Bob(delay: Double(i) * 0.37))
                    .transition(.asymmetric(insertion: .scale(scale: 0.6).combined(with: .opacity),
                                            removal: .scale(scale: 0.85).combined(with: .opacity)))
            }
        }
        .onReceive(timer) { _ in
            let unused = POOL.filter { !shown.contains($0) }
            guard let next = unused.randomElement() else { return }
            withAnimation(.spring(duration: 0.7, bounce: 0.25)) { shown[Int.random(in: 0..<shown.count)] = next }
        }
    }
}

private struct Bob: ViewModifier {
    let delay: Double
    @State private var up = false
    func body(content: Content) -> some View {
        content
            .offset(y: up ? -3 : 3)
            .onAppear {
                withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true).delay(delay)) { up = true }
            }
    }
}

/// One dashboard number: the latest value, its change, a sparkline, and the table. Tapping asks about it.
private struct Tile: View {
    @Environment(\.pal) private var pal
    let ind: Indicator
    let lang: Lang
    let ask: Ask

    var body: some View {
        let unit = ind.unit(lang)
        let pct = ind.unit.en == "%"
        Button { ask(ind.question(lang), nil) } label: {
            VStack(alignment: .leading, spacing: 6) {
                Text(ind.label(lang)).font(.caption.weight(.medium)).foregroundStyle(pal.text2).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text(fmtNum(ind.value, percent: pct, lang)).font(.system(size: 26, weight: .semibold, design: .rounded))
                        .monospacedDigit().foregroundStyle(pal.text).minimumScaleFactor(0.6).lineLimit(1)
                    if pct { Text("%").font(.subheadline).foregroundStyle(pal.text3) }
                }
                HStack(spacing: 6) {
                    Text(fmtPeriod(ind.period, lang)).foregroundStyle(pal.text3)
                    if let (text, up) = delta(pct) {
                        Label(text, systemImage: up ? "arrow.up.right" : "arrow.down.right")
                            .labelStyle(.titleAndIcon)
                            .foregroundStyle(pal.text2)
                    }
                }
                .font(.caption2)
                MiniChart(series: ind.series).frame(height: 42).padding(.vertical, 2)
                Text([ind.table["id"] ?? "", pct ? "" : unit].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption2).foregroundStyle(pal.text3).lineLimit(1)
            }
            .padding(14)
            .background(pal.card, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .shadow(color: .black.opacity(0.05), radius: 10, y: 4)
        }
        .buttonStyle(.plain)
    }

    /// Percentages change by points; everything else by percent.
    private func delta(_ pct: Bool) -> (String, Bool)? {
        guard let prev = ind.previous else { return nil }
        let d = pct ? ind.value - prev : (prev == 0 ? 0 : (ind.value - prev) / abs(prev) * 100)
        let sign = d >= 0 ? "+" : "−"
        return (sign + fmtNum(abs(d), percent: true, lang) + (pct ? " " + Words(lang: lang).pts : "%"), d >= 0)
    }
}
