// Past conversations, saved on this device only: open one to read or continue it, swipe to delete.
import SwiftUI

struct HistoryView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let isl = model.ui == .is
        NavigationStack {
            Group {
                if model.conversations.isEmpty {
                    ContentUnavailableView(isl ? "Engin samtöl enn" : "No conversations yet", systemImage: "bubble.left.and.bubble.right",
                                           description: Text(isl ? "Samtöl vistast hér í símanum þegar svar berst."
                                                                 : "Conversations are kept here on this device once they have an answer."))
                } else {
                    List {
                        ForEach(model.conversations) { c in
                            Button {
                                model.open(c)
                                dismiss()
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(c.title).lineLimit(2).foregroundStyle(pal.text)
                                    Text("\(c.exchanges.count) \(isl ? (c.exchanges.count == 1 ? "spurning" : "spurningar") : (c.exchanges.count == 1 ? "question" : "questions")) · \(c.updated.formatted(.relative(presentation: .named)))")
                                        .font(.caption).foregroundStyle(pal.text3)
                                }
                                .padding(.vertical, 2)
                            }
                            .listRowBackground(pal.card)
                        }
                        .onDelete { rows in model.delete(rows.map { model.conversations[$0].id }) }
                    }
                    .scrollContentBackground(.hidden)
                }
            }
            .background(pal.bg)
            .navigationTitle(isl ? "Fyrri samtöl" : "Past conversations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button(isl ? "Lokið" : "Done") { dismiss() } }
                if !model.conversations.isEmpty { ToolbarItem(placement: .topBarLeading) { EditButton() } }
            }
        }
        .preferredColorScheme(model.colorScheme)
    }
}
