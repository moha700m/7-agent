import SwiftUI

@main
struct CyberMicApp: App {
    @StateObject private var bridge = CyberMicBridge()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(bridge)
                .task {
                    await bridge.start()
                }
        }
    }
}
