import SwiftUI
import AVFAudio
import Accessibility

struct ContentView: View {
    @EnvironmentObject var bridge: CyberMicBridge

    var body: some View {
        NavigationStack {
            Form {
                Section("الحالة") {
                    LabeledContent("IP الآيفون", value: bridge.localIP)
                    LabeledContent("UDP", value: "8767")
                    LabeledContent("استقبال الكمبيوتر", value: bridge.isListening ? "شغال" : "متوقف")
                    LabeledContent("اتصال المرسل", value: bridge.lastPacketAgo)
                    LabeledContent("إذن الحقن", value: bridge.permissionText)
                    LabeledContent("المايك الرقمي", value: bridge.injectionEnabled ? "مفعّل" : "غير مفعّل")
                }

                Section("الإعداد مرة واحدة") {
                    Button("فتح Add Audio in Calls") {
                        Task {
                            do {
                                try await AccessibilitySettings.openSettings(for: .allowAppsToAddAudioToCalls)
                            } catch {
                                bridge.message = "افتح يدويًا: Settings → Accessibility → Audio & Visual → Add Audio in Calls"
                            }
                        }
                    }

                    Button("طلب إذن Microphone Injection") {
                        Task { await bridge.requestInjectionPermission() }
                    }
                }

                Section("التشغيل") {
                    Button(bridge.injectionEnabled ? "إيقاف المايك الرقمي" : "تشغيل المايك الرقمي") {
                        bridge.setInjection(!bridge.injectionEnabled)
                    }

                    Button(bridge.isListening ? "إيقاف استقبال الكمبيوتر" : "تشغيل استقبال الكمبيوتر") {
                        bridge.isListening ? bridge.stopReceiver() : bridge.startReceiver()
                    }
                }

                Section("الربط") {
                    Text("في Windows خل خرج ChatGPT على CABLE-B Input. برنامج CyberMic Sender يقرأ CABLE-B Output ويرسل الصوت إلى IP الظاهر فوق.")
                    Text("إذا كانت المكالمة تستخدم Apple voice processing، iOS يضيف صوت CyberMic إلى مسار الميكروفون.")
                }

                if !bridge.message.isEmpty {
                    Section("معلومة") {
                        Text(bridge.message)
                    }
                }
            }
            .navigationTitle("CyberMic")
        }
    }
}
