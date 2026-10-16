import Foundation
import AVFAudio
import Network
import Darwin

@MainActor
final class CyberMicBridge: ObservableObject {
    @Published var isListening = false
    @Published var injectionEnabled = false
    @Published var permissionText = "غير معروف"
    @Published var message = ""
    @Published var localIP = "—"
    @Published var lastPacketDate: Date?

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var listener: NWListener?
    private var connections: [NWConnection] = []
    private let queue = DispatchQueue(label: "CyberMic.Network")
    private let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48_000, channels: 1, interleaved: false)!

    var lastPacketAgo: String {
        guard let d = lastPacketDate else { return "ما وصل صوت" }
        let s = Int(Date().timeIntervalSince(d))
        return s < 2 ? "يوصل الآن" : "قبل \(s) ث"
    }

    func start() async {
        localIP = Self.wifiIPv4() ?? "غير متصل Wi‑Fi"
        configureAudio()
        updatePermission()
        startReceiver()
    }

    func updatePermission() {
        switch AVAudioApplication.shared.microphoneInjectionPermission {
        case .granted: permissionText = "مسموح"
        case .denied: permissionText = "مرفوض"
        case .undetermined: permissionText = "يحتاج إذن"
        case .serviceDisabled: permissionText = "Add Audio in Calls مقفل"
        @unknown default: permissionText = "غير معروف"
        }
    }

    func requestInjectionPermission() async {
        let p = await AVAudioApplication.requestMicrophoneInjectionPermission()
        updatePermission()
        if p == .granted {
            message = "تم السماح. الآن اضغط تشغيل المايك الرقمي."
        } else {
            message = "ما تم السماح. تأكد من Add Audio in Calls في إعدادات Accessibility."
        }
    }

    func setInjection(_ enabled: Bool) {
        do {
            try AVAudioSession.sharedInstance().setPreferredMicrophoneInjectionMode(enabled ? .spokenAudio : .none)
            injectionEnabled = enabled
            message = enabled ? "تم طلب حقن صوت CyberMic داخل المكالمات المدعومة." : "تم إيقاف الحقن."
        } catch {
            injectionEnabled = false
            message = "تعذر تشغيل الحقن: \(error.localizedDescription)"
        }
    }

    private func configureAudio() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio, options: [.mixWithOthers])
            try session.setActive(true)

            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)
            try engine.start()
            player.play()
        } catch {
            message = "Audio Engine: \(error.localizedDescription)"
        }
    }

    func startReceiver() {
        guard listener == nil else { return }
        do {
            let l = try NWListener(using: .udp, on: 8767)
            l.newConnectionHandler = { [weak self] connection in
                guard let self else { return }
                self.connections.append(connection)
                connection.start(queue: self.queue)
                self.receive(on: connection)
            }
            l.stateUpdateHandler = { [weak self] state in
                Task { @MainActor in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        self.isListening = true
                        self.message = "CyberMic ينتظر صوت الكمبيوتر على UDP 8767."
                    case .failed(let e):
                        self.isListening = false
                        self.message = "Network: \(e.localizedDescription)"
                    default: break
                    }
                }
            }
            listener = l
            l.start(queue: queue)
        } catch {
            message = "تعذر فتح UDP 8767: \(error.localizedDescription)"
        }
    }

    func stopReceiver() {
        listener?.cancel()
        listener = nil
        connections.forEach { $0.cancel() }
        connections.removeAll()
        isListening = false
    }

    private nonisolated func receive(on connection: NWConnection) {
        connection.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            if let data, !data.isEmpty {
                Task { @MainActor in
                    self.enqueuePCM(data)
                    self.lastPacketDate = Date()
                }
            }
            if error == nil {
                self.receive(on: connection)
            }
        }
    }

    private func enqueuePCM(_ data: Data) {
        let sampleCount = data.count / MemoryLayout<Int16>.size
        guard sampleCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sampleCount)),
              let dst = buffer.int16ChannelData?[0] else { return }

        buffer.frameLength = AVAudioFrameCount(sampleCount)
        data.withUnsafeBytes { raw in
            if let src = raw.baseAddress {
                memcpy(dst, src, sampleCount * MemoryLayout<Int16>.size)
            }
        }
        player.scheduleBuffer(buffer)
        if !player.isPlaying { player.play() }
    }

    static func wifiIPv4() -> String? {
        var address: String?
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }

        for ptr in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let interface = ptr.pointee
            let family = interface.ifa_addr.pointee.sa_family
            if family == UInt8(AF_INET) {
                let name = String(cString: interface.ifa_name)
                if name == "en0" {
                    var addr = interface.ifa_addr.pointee
                    var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                    getnameinfo(&addr, socklen_t(interface.ifa_addr.pointee.sa_len),
                                &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST)
                    address = String(cString: host)
                }
            }
        }
        return address
    }
}
