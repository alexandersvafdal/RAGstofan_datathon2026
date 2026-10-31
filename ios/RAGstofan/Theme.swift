// The website's eight palettes (frontend/style.css), each in light and dark.
import SwiftUI

struct Palette {
    let bg, card, text, text2, text3, accent, accent2, accentInk, grid, series1, series2, series3, warn: Color
    var soft: Color { accent.opacity(0.12) }
    var series: [Color] { [series1, series2, series3, accent2, text3, text2] }
}

enum PaletteName: String, CaseIterable, Identifiable {
    case warm, graphite, blue, navy, glacier, aurora, lava, hagstofan
    var id: String { rawValue }
    var title: String { self == .hagstofan ? "Hagstofan" : rawValue.capitalized }
}

private func c(_ hex: UInt32) -> Color {
    Color(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255)
}

private func p(_ v: [UInt32]) -> Palette {
    Palette(bg: c(v[0]), card: c(v[1]), text: c(v[2]), text2: c(v[3]), text3: c(v[4]), accent: c(v[5]), accent2: c(v[6]),
            accentInk: c(v[7]), grid: c(v[2]).opacity(0.08), series1: c(v[8]), series2: c(v[9]), series3: c(v[10]), warn: c(v[11]))
}

//                       bg        card      text      text2     text3     accent    accent2   ink       s1        s2        s3        warn
private let table: [PaletteName: (light: Palette, dark: Palette)] = [
    .warm: (p([0xf6f3ee, 0xfffdf9, 0x2b2620, 0x5a5248, 0x8c8377, 0x3f6b4d, 0x6e9a78, 0xffffff, 0x3f6b4d, 0xc27c3e, 0x6f7f99, 0xf6ecd6]),
            p([0x171511, 0x211e19, 0xf2ede4, 0xcfc7ba, 0x9a9184, 0x8fbf9a, 0x5f8f6b, 0x14180f, 0x8fbf9a, 0xe0a06a, 0x9fb0cc, 0x2e2718])),
    .graphite: (p([0xf5f5f7, 0xffffff, 0x1d1d1f, 0x48484a, 0x86868b, 0x3a3a3c, 0x8e8e93, 0xffffff, 0x3a3a3c, 0xa1a1a6, 0x6e6e73, 0xf2f2f4]),
                p([0x000000, 0x1c1c1e, 0xf5f5f7, 0xc7c7cc, 0x8e8e93, 0xe5e5ea, 0x8e8e93, 0x1c1c1e, 0xe5e5ea, 0x8e8e93, 0x636366, 0x2c2c2e])),
    .blue: (p([0xf5f5f7, 0xffffff, 0x1d1d1f, 0x424245, 0x86868b, 0x0071e3, 0x5ea8ff, 0xffffff, 0x0071e3, 0xff9f0a, 0x30a46c, 0xfff8e6]),
            p([0x000000, 0x1c1c1e, 0xf5f5f7, 0xc7c7cc, 0x8e8e93, 0x2997ff, 0x0a5cc2, 0xffffff, 0x2997ff, 0xffb340, 0x3dd68c, 0x2a2414])),
    .navy: (p([0xf3f6f8, 0xffffff, 0x0f2433, 0x3b5163, 0x7a8b98, 0x0d4f74, 0x3f8fb5, 0xffffff, 0x0d4f74, 0xe07b39, 0x3a9a8a, 0xeef3e6]),
            p([0x0b141b, 0x132029, 0xe8f0f5, 0xb9c8d3, 0x7f93a2, 0x6fb6dc, 0x2f7aa3, 0x0b141b, 0x6fb6dc, 0xf0a06a, 0x6cc5b3, 0x1f2a1c])),
    .glacier: (p([0xf2f7f8, 0xffffff, 0x0e2a30, 0x3a5960, 0x7a9399, 0x137c8b, 0x5fb3bf, 0xffffff, 0x137c8b, 0xd9822b, 0x6a6fb5, 0xeaf4ef]),
               p([0x0a1517, 0x112024, 0xe6f3f5, 0xb5cdd1, 0x7c979c, 0x6fd0dc, 0x2b8f9c, 0x0a1517, 0x6fd0dc, 0xf0a865, 0xa3a8f0, 0x1c2a22])),
    .aurora: (p([0xf6f4fa, 0xffffff, 0x1f1830, 0x4c4462, 0x8a839c, 0x5b3fb8, 0x9a85e0, 0xffffff, 0x5b3fb8, 0x2fa58a, 0xd9708f, 0xf3eefa]),
              p([0x110e1a, 0x1b1727, 0xefeaf9, 0xc6bedb, 0x8d85a3, 0xa994ff, 0x6a52d1, 0x110e1a, 0xa994ff, 0x5fd6b6, 0xf093b0, 0x251f33])),
    .lava: (p([0xf7f2ef, 0xfffcfa, 0x2e1f19, 0x5e4a40, 0x957f74, 0xb4532a, 0xe08a5f, 0xffffff, 0xb4532a, 0x3f6f8a, 0x7a8f4a, 0xf7ead8]),
            p([0x171110, 0x221917, 0xf6ece7, 0xd7c3b9, 0xa08b80, 0xf08a5d, 0xb4532a, 0x171110, 0xf08a5d, 0x7fb2cf, 0xb5c97f, 0x2d2316])),
    .hagstofan: (p([0xeef8fb, 0xffffff, 0x0b3f4d, 0x285971, 0x6f8f9c, 0x285971, 0x00c8ff, 0xffffff, 0x285971, 0xe0b37b, 0x00a8d8, 0xf8eedf]),
                 p([0x072a33, 0x0e3a46, 0xeef8fb, 0xbcd6de, 0x7fa3ae, 0x00c8ff, 0x285971, 0x072a33, 0x00c8ff, 0xe0b37b, 0x7fb7d0, 0x3a3020])),
]

func palette(_ name: PaletteName, dark: Bool) -> Palette {
    let pair = table[name] ?? table[.warm]!
    return dark ? pair.dark : pair.light
}

private struct PaletteKey: EnvironmentKey { static let defaultValue = palette(.warm, dark: false) }

extension EnvironmentValues {
    var pal: Palette {
        get { self[PaletteKey.self] }
        set { self[PaletteKey.self] = newValue }
    }
}

/// A rounded card on the palette's card colour, the building block of every screen.
struct Card<Content: View>: View {
    @Environment(\.pal) private var pal
    var padding: CGFloat = 18
    @ViewBuilder let content: Content
    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(pal.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
            .shadow(color: .black.opacity(0.06), radius: 14, y: 6)
    }
}

/// A rounded pill, used for example questions, follow-ups and retry.
struct PillStyle: ButtonStyle {
    @Environment(\.pal) private var pal
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline)
            .multilineTextAlignment(.leading)
            .foregroundStyle(pal.text)
            .padding(.horizontal, 14)
            .padding(.vertical, 9)
            .background(pal.card, in: Capsule())
            .overlay(Capsule().stroke(pal.text.opacity(0.1)))
            .shadow(color: .black.opacity(0.05), radius: 6, y: 3)
            .scaleEffect(configuration.isPressed ? 0.95 : 1)
            .animation(.snappy(duration: 0.15), value: configuration.isPressed)
    }
}

/// Lays children out in rows that wrap, like inline text.
struct Flow: Layout {
    var spacing: CGFloat = 8
    var centered = false

    private struct Line { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func lines(_ maxWidth: CGFloat, _ subviews: Subviews) -> [Line] {
        var out = [Line()]
        for (i, v) in subviews.enumerated() {
            let s = v.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
            if !out[out.count - 1].items.isEmpty && out[out.count - 1].width + spacing + s.width > maxWidth { out.append(Line()) }
            var l = out[out.count - 1]
            l.width += (l.items.isEmpty ? 0 : spacing) + s.width
            l.height = max(l.height, s.height)
            l.items.append(i)
            out[out.count - 1] = l
        }
        return out
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let ls = lines(proposal.width ?? .infinity, subviews)
        let h = ls.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(ls.count - 1, 0))
        return CGSize(width: proposal.width ?? (ls.map(\.width).max() ?? 0), height: h)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for l in lines(bounds.width, subviews) {
            var x = centered ? bounds.minX + (bounds.width - l.width) / 2 : bounds.minX
            for i in l.items {
                let s = subviews[i].sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
                subviews[i].place(at: CGPoint(x: x, y: y + (l.height - s.height) / 2), proposal: ProposedViewSize(s))
                x += s.width + spacing
            }
            y += l.height + spacing
        }
    }
}

/// A small capsule label.
struct Chip: View {
    @Environment(\.pal) private var pal
    let text: Text
    var icon: String?
    var strong = false
    var body: some View {
        HStack(spacing: 4) {
            if let icon { Image(systemName: icon) }
            text
        }
        .font(.caption)
        .foregroundStyle(strong ? pal.accent : pal.text2)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(strong ? pal.soft : pal.text.opacity(0.05), in: Capsule())
    }
}

/// A small caps heading over one part of an opened Disclosure.
struct DetailSection<Content: View>: View {
    @Environment(\.pal) private var pal
    let title: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased()).font(.caption2.weight(.semibold)).tracking(0.8).foregroundStyle(pal.text3)
            content
        }
    }
}

/// A section that opens on tap, like the website's <details>.
struct Disclosure<Content: View>: View {
    @Environment(\.pal) private var pal
    let title: String
    @ViewBuilder let content: Content
    @State private var open = false
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button { withAnimation(.snappy) { open.toggle() } } label: {
                HStack {
                    Text(title).font(.footnote.weight(.medium))
                    Spacer()
                    Image(systemName: "chevron.down").font(.caption2.weight(.semibold)).rotationEffect(.degrees(open ? 180 : 0))
                }
                .foregroundStyle(pal.text3)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open { content.transition(.opacity) }
        }
        .padding(.top, 12)
        .overlay(alignment: .top) { Rectangle().fill(pal.text.opacity(0.08)).frame(height: 1) }
    }
}
