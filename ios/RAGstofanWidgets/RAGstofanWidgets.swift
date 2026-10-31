// The Live Activity for a question: its steps on the lock screen and in the Dynamic Island, then the answer.
import ActivityKit
import SwiftUI
import WidgetKit

@main
struct RAGstofanWidgets: WidgetBundle {
    var body: some Widget { QuestionLiveActivity() }
}

private let accent = Color(red: 0.0, green: 0.78, blue: 1.0)   // Hagstofan's light blue reads on dark and light

struct QuestionLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: QuestionActivity.self) { ctx in
            LockScreen(attrs: ctx.attributes, state: ctx.state)
                .padding(16)
                .activityBackgroundTint(Color.black.opacity(0.65))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { ctx in
            let s = ctx.state, done = s.answer != nil
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Bars(done: done).frame(width: 26, height: 22).padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    StepCount(state: s).padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(ctx.attributes.question).font(.subheadline.weight(.semibold)).lineLimit(1)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        if let a = s.answer { Text(a).font(.caption).lineLimit(3) }
                        else {
                            Progress(step: s.step)
                            Text(s.stepName + (s.detail.isEmpty ? "" : " · \(s.detail)")).font(.caption2).foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                    }
                }
            } compactLeading: {
                Bars(done: done).frame(width: 16, height: 14)
            } compactTrailing: {
                StepCount(state: s)
            } minimal: {
                ZStack {
                    Circle().stroke(Color.white.opacity(0.2), lineWidth: 2.5)
                    Circle().trim(from: 0, to: done ? 1 : CGFloat(s.step) / CGFloat(QuestionActivity.steps))
                        .stroke(s.failed ? .orange : accent, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                }
                .padding(2)
            }
            .keylineTint(accent)
        }
    }
}

private struct LockScreen: View {
    let attrs: QuestionActivity
    let state: QuestionActivity.ContentState
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Bars(done: state.answer != nil).frame(width: 22, height: 18)
                Text("RAGstofan").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                if state.answer == nil && !state.failed {
                    Text(attrs.started, style: .timer).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        .multilineTextAlignment(.trailing).frame(width: 56)
                }
            }
            Text(attrs.question).font(.headline).foregroundStyle(.white).lineLimit(2)
            if let a = state.answer {
                Text(a).font(.subheadline).foregroundStyle(.white.opacity(0.9)).lineLimit(3)
            } else {
                Progress(step: state.step, failed: state.failed)
                Text(state.stepName + (state.detail.isEmpty ? "" : " · \(state.detail)"))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }
}

/// Five segments, one per step, filling as the question moves on.
private struct Progress: View {
    let step: Int
    var failed = false
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<QuestionActivity.steps, id: \.self) { i in
                Capsule()
                    .fill(i < step ? (failed ? Color.orange : accent) : i == step ? accent.opacity(0.45) : Color.white.opacity(0.18))
                    .frame(height: 5)
            }
        }
    }
}

private struct StepCount: View {
    let state: QuestionActivity.ContentState
    var body: some View {
        if state.answer != nil { Image(systemName: "checkmark.circle.fill").foregroundStyle(accent) }
        else if state.failed { Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange) }
        else { Text("\(min(state.step + 1, QuestionActivity.steps))/\(QuestionActivity.steps)").font(.caption.monospacedDigit()).foregroundStyle(accent) }
    }
}

/// The logo's three rising bars.
private struct Bars: View {
    let done: Bool
    var body: some View {
        GeometryReader { g in
            HStack(alignment: .bottom, spacing: g.size.width * 0.12) {
                ForEach([0.45, 0.7, 1.0], id: \.self) { h in
                    RoundedRectangle(cornerRadius: g.size.width * 0.08)
                        .fill(done ? accent : accent.opacity(0.4 + h * 0.6))
                        .frame(height: g.size.height * h)
                }
            }
            .frame(maxHeight: .infinity, alignment: .bottom)
        }
    }
}
