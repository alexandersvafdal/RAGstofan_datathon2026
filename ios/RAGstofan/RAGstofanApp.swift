// RAGstofan for iPhone and iPad: the website's chat, charts and dashboard, talking to the same server.
import SwiftUI

@main
struct RAGstofanApp: App {
    @State private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            RootView().environment(model)
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var system
    var body: some View {
        let pal = palette(model.paletteName, dark: (model.colorScheme ?? system) == .dark)
        ChatView()
            .environment(\.pal, pal)
            .tint(pal.accent)
            .preferredColorScheme(model.colorScheme)
            .task { await model.connect() }
            .fullScreenCover(isPresented: Binding(get: { !model.onboarded }, set: { model.onboarded = !$0 })) {
                OnboardingView().environment(model).environment(\.pal, pal).tint(pal.accent)
            }
    }
}
