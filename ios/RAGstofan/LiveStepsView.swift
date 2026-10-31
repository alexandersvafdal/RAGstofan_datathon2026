// Live progress: each step lights up when the server reports it done, with what it found (frontend liveView).
import SwiftUI

private let order = ["plan", "retrieve", "select", "fetch", "compose"]

/// What the steps so far add up to.
private struct LiveState {
    var done = Set<String>()
    var current: String?
    var bad = false
    var ms: [String: Int] = [:]
    var steps: [String: [Step]] = [:]
    var context: String?

    init(_ all: [Step]) {
        var parts = 1, count: [String: Int] = [:]
        for s in all {
            if s.name == "context" { context = s.detail; continue }   // a follow-up rewritten from the previous turn
            guard order.contains(s.name) else { bad = true; continue }
            if s.name == "plan" { parts = max(1, s.detail.components(separatedBy: " | ").count) }
            count[s.name, default: 0] += 1
            ms[s.name, default: 0] += s.ms
            steps[s.name, default: []].append(s)
            // retrieve, select and fetch run once per sub-question
            if s.name == "plan" || s.name == "compose" || count[s.name]! >= parts { done.insert(s.name) }
        }
        current = order.first { !done.contains($0) }
    }
}

struct LiveStepsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let ex: Exchange

    var body: some View {
        let w = Words(lang: ex.lang)
        let st = LiveState(ex.steps)
        Card {
            VStack(alignment: .leading, spacing: 0) {
                if !ex.fast {
                    // stop waiting: the fast model answers with thinking off. At the top, so it stays put as steps fill in
                    HStack {
                        Spacer()
                        Button { model.answerNow(ex) } label: {
                            Label(ex.lang == .is ? "Svara strax" : "Answer now", systemImage: "bolt.fill")
                        }
                        .buttonStyle(PillStyle())
                    }
                    .padding(.bottom, 4)
                }
                ForEach(Array(order.enumerated()), id: \.offset) { i, name in
                    let done = st.done.contains(name), now = st.current == name
                    HStack(alignment: .top, spacing: 12) {
                        StepDot(done: done, now: now, bad: now && st.bad)
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(w.steps[i])
                                    .font(.subheadline.weight(now ? .semibold : .regular))
                                    .foregroundStyle(done || now ? pal.text : pal.text3)
                                Spacer()
                                if now {
                                    TimelineView(.periodic(from: ex.stepStarted, by: 0.1)) { ctx in
                                        Text(String(format: "%.1f s", max(0, ctx.date.timeIntervalSince(ex.stepStarted))))
                                    }
                                    .font(.caption.monospacedDigit()).foregroundStyle(pal.text3)
                                } else if done, let ms = st.ms[name] {
                                    Text(fmtMs(ms)).font(.caption.monospacedDigit()).foregroundStyle(pal.text3)
                                }
                            }
                            details(name, w, st)
                        }
                    }
                    .padding(.vertical, 8)
                    .animation(.snappy, value: done)
                }
            }
        }
    }

    @ViewBuilder
    private func details(_ name: String, _ w: Words, _ st: LiveState) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if name == "plan", let c = st.context { line(w.followup(c)) }
            ForEach(Array((st.steps[name] ?? []).enumerated()), id: \.offset) { _, s in detail(s, w) }
            if st.bad && st.current == name { line(w.failed).foregroundStyle(.red) }
        }
        .transition(.opacity)
    }

    private func line(_ s: String) -> some View {
        Text(s).font(.footnote).foregroundStyle(pal.text2).fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private func detail(_ s: Step, _ w: Words) -> some View {
        let L = ex.lang
        switch s.name {
        case "plan":
            let qs = s.detail.components(separatedBy: " | ")
            line(qs.count > 1 ? w.split(qs.count) : w.read(qs[0]))
        case "retrieve":
            let m = firstGroups(#"^(\S+) from \[(.*?)\]"#, s.detail)
            let via = firstGroups(#"via ([a-z ]+?)(?: ·|$)"#, s.detail)?[1]
            let concepts = firstGroups(#"concepts: (.+)$"#, s.detail)?[1]
            let picked = m?[1] ?? s.detail
            line(w.searched(model.health?.tablesInCatalog ?? 1857, picked)
                 + (via.map { " · " + w.via($0) } ?? "") + (concepts.map { " (\($0))" } ?? ""))
            if let v = firstGroups(#"votes (\d+/\d+)"#, s.detail)?[1] { line(w.votes(v)) }
            if let cands = m?[2].components(separatedBy: ", ").prefix(8), cands.count > 1 {
                Flow(spacing: 5) {
                    ForEach(Array(cands.enumerated()), id: \.offset) { _, c in
                        Text(c).font(.caption2.monospaced())
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .foregroundStyle(c == picked ? pal.accentInk : pal.text2)
                            .background(c == picked ? pal.accent : pal.text.opacity(0.06), in: Capsule())
                    }
                }
            }
        case "select":
            let sel = readSelection(s.detail, L)
            if !sel.isEmpty { line(sel) }
            if let v = firstGroups(#"votes: ([^'"\]]+)"#, s.detail)?[1] { line(w.votes(v)) }
        case "fetch":
            let n = Int(firstGroups(#"^(\d+) rows"#, s.detail)?[1] ?? "0") ?? 0
            let parts = s.detail.components(separatedBy: "; ")
            line(w.numbers(n) + (parts.count > 1 ? " · " + prettyPeriods(parts[1], L) : ""))
        case "compose":
            line(s.detail == "model" ? w.checked : w.template)
        default:
            EmptyView()
        }
    }
}

/// Done: a filled tick. In progress: a turning arc. Waiting: a faint ring.
private struct StepDot: View {
    @Environment(\.pal) private var pal
    let done: Bool, now: Bool, bad: Bool
    var body: some View {
        ZStack {
            if done {
                Circle().fill(pal.accent)
                Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)).foregroundStyle(pal.accentInk)
                    .transition(.scale.combined(with: .opacity))
            } else if now {
                Circle().stroke(pal.text.opacity(0.1), lineWidth: 2)
                TimelineView(.animation) { ctx in
                    Circle().trim(from: 0, to: 0.3)
                        .stroke(bad ? Color.red : pal.accent, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                        .rotationEffect(.degrees(ctx.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1) * 360))
                }
            } else {
                Circle().stroke(pal.text.opacity(0.15), lineWidth: 1.5)
            }
        }
        .frame(width: 20, height: 20)
        .padding(.top, 1)
    }
}
