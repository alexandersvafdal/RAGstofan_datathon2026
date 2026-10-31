// The hamburger menu: a new conversation, private mode, and past conversations with search. Conversations stay on
// this device; in private mode nothing is saved.
import SwiftUI

struct Drawer: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    let close: () -> Void
    @State private var query = ""

    var body: some View {
        let isl = model.ui == .is
        let found = model.search(query)
        GeometryReader { g in
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("RAGstofan").font(.title2.bold()).foregroundStyle(pal.text)
                    Spacer()
                    Button(action: close) { Image(systemName: "xmark").font(.headline).foregroundStyle(pal.text3) }
                        .accessibilityLabel(isl ? "Loka" : "Close")
                }
                Button {
                    model.reset()
                    close()
                } label: {
                    Label(isl ? "Nýtt samtal" : "New conversation", systemImage: "square.and.pencil")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .foregroundStyle(pal.accentInk)
                        .background(pal.accent, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                }
                .buttonStyle(.plain)

                // the whole card switches it, not just the little switch
                Button { withAnimation(.spring(duration: 0.35)) { model.privateMode.toggle() } } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Label(isl ? "Einkahamur" : "Private mode", systemImage: model.privateMode ? "eye.slash.fill" : "eye.slash")
                                .font(.subheadline).foregroundStyle(pal.text)
                            Spacer()
                            Toggle("", isOn: .constant(model.privateMode)).labelsHidden().tint(pal.accent).allowsHitTesting(false)
                        }
                        Text(isl ? "Samtöl vistast ekki á meðan kveikt er á honum." : "Conversations are not saved while it is on.")
                            .font(.caption).foregroundStyle(pal.text3)
                    }
                    .padding(12)
                    .background(model.privateMode ? pal.soft : pal.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .sensoryFeedback(.selection, trigger: model.privateMode)

                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass").foregroundStyle(pal.text3)
                    TextField(isl ? "Leita í samtölum" : "Search conversations", text: $query)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .foregroundStyle(pal.text)
                    if !query.isEmpty {
                        Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(pal.text3) }
                    }
                }
                .padding(.horizontal, 12).frame(height: 40)
                .background(pal.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))

                if found.isEmpty {
                    Text(query.isEmpty ? (isl ? "Engin vistuð samtöl enn." : "No saved conversations yet.")
                                       : (isl ? "Ekkert fannst." : "Nothing found."))
                        .font(.footnote).foregroundStyle(pal.text3).padding(.top, 6)
                    Spacer()
                } else {
                    List {
                        ForEach(groups(found, isl), id: \.0) { title, items in
                            Section {
                                ForEach(items) { c in row(c) }
                                    .onDelete { rows in model.delete(rows.map { items[$0].id }) }
                            } header: {
                                Text(title).font(.caption.weight(.semibold)).foregroundStyle(pal.text3)
                            }
                        }
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                    .animation(.snappy, value: query)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .frame(width: min(340, g.size.width * 0.86), alignment: .topLeading)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(pal.bg.ignoresSafeArea())
            .shadow(color: .black.opacity(0.25), radius: 24, x: 8)
        }
    }

    private func row(_ c: Conversation) -> some View {
        Button {
            model.open(c)
            close()
        } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(c.title).font(.subheadline).foregroundStyle(pal.text).lineLimit(2)
                if let hit = snippet(c) {
                    Text(hit).font(.caption).foregroundStyle(pal.text2).lineLimit(2)
                }
                Text("\(c.exchanges.count) · \(c.updated.formatted(.relative(presentation: .named)))")
                    .font(.caption2).foregroundStyle(pal.text3)
            }
            .padding(.vertical, 2)
        }
        .listRowBackground(Color.clear)
        .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
    }

    /// While searching: the answer text that matched, so it is clear why the conversation is listed.
    private func snippet(_ c: Conversation) -> String? {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return nil }
        return c.exchanges.first { !$0.question.localizedStandardContains(q) && $0.answer.text.localizedStandardContains(q) }?.answer.text
    }

    /// Today, Yesterday, This week, Earlier.
    private func groups(_ list: [Conversation], _ isl: Bool) -> [(String, [Conversation])] {
        let cal = Calendar.current
        func bucket(_ d: Date) -> Int {
            if cal.isDateInToday(d) { return 0 }
            if cal.isDateInYesterday(d) { return 1 }
            if let w = cal.dateInterval(of: .weekOfYear, for: Date()), w.contains(d) { return 2 }
            return 3
        }
        let names = isl ? ["Í dag", "Í gær", "Þessa viku", "Eldra"] : ["Today", "Yesterday", "This week", "Earlier"]
        let byBucket = Dictionary(grouping: list) { bucket($0.updated) }
        return (0...3).compactMap { b in byBucket[b].map { (names[b], $0) } }
    }
}
