// Asking by voice and hearing answers: records the question on the phone, sends it to the Spark's Whisper, and plays
// the Spark's Piper reading of an answer.
import AVFoundation
import SwiftUI

@MainActor @Observable
final class Voice: NSObject, AVAudioPlayerDelegate {
    enum State: Equatable { case idle, listening, transcribing }
    var state: State = .idle
    var level: Double = 0          // 0...1, the microphone's loudness while listening
    var speaking: String?          // what is being read aloud: an exchange's id, or a voice's sample
    var loadingSpeech: String?
    var error: String?

    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var meter: Timer?
    private let file = FileManager.default.temporaryDirectory.appendingPathComponent("question.m4a")

    func startListening() async {
        error = nil
        stopSpeaking()
        guard await AVAudioApplication.requestRecordPermission() else {
            error = "Microphone access is off. Turn it on in Settings › RAGstofan."
            return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothHFP])
            try session.setActive(true)
            let settings: [String: Any] = [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16_000,
                                           AVNumberOfChannelsKey: 1, AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue]
            recorder = try AVAudioRecorder(url: file, settings: settings)
            recorder?.isMeteringEnabled = true
            recorder?.record()
            state = .listening
            meter = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self, let r = self.recorder else { return }
                    r.updateMeters()
                    let db = Double(r.averagePower(forChannel: 0))          // -160...0
                    self.level = max(0, min(1, (db + 50) / 50))
                }
            }
        } catch {
            self.error = error.localizedDescription
            state = .idle
        }
    }

    /// Stop recording and return what was said, or nil.
    func finishListening(api: API?, language: String?) async -> String? {
        meter?.invalidate()
        recorder?.stop()
        recorder = nil
        level = 0
        guard let api else { state = .idle; return nil }
        state = .transcribing
        defer { state = .idle }
        do {
            let text = try await api.stt(file, language: language)
            return text.isEmpty ? nil : text
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }

    func cancelListening() {
        meter?.invalidate()
        recorder?.stop()
        recorder = nil
        level = 0
        state = .idle
    }

    /// Read an answer aloud; reading the same one again stops it.
    func toggleSpeaking(_ id: String, text: String, language: String, voice: String? = nil, api: API?) async {
        if speaking == id || loadingSpeech == id { stopSpeaking(); return }
        stopSpeaking()
        guard let api else { return }
        loadingSpeech = id
        do {
            let wav = try await api.tts(text, language: language, voice: voice)
            guard loadingSpeech == id else { return }
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            player = try AVAudioPlayer(data: wav)
            player?.delegate = self
            player?.play()
            speaking = id
        } catch {
            self.error = error.localizedDescription
        }
        loadingSpeech = nil
    }

    func stopSpeaking() {
        player?.stop()
        player = nil
        speaking = nil
        loadingSpeech = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.speaking = nil }
    }
}

/// The microphone's loudness as five bars that dance while listening.
struct LevelBars: View {
    @Environment(\.pal) private var pal
    let level: Double
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<5, id: \.self) { i in
                let shape = [0.45, 0.75, 1.0, 0.75, 0.45][i]
                Capsule()
                    .fill(pal.accent)
                    .frame(width: 4, height: 6 + 22 * level * shape)
            }
        }
        .frame(height: 28)
        .animation(.spring(duration: 0.15), value: level)
    }
}

/// The voices for English and Icelandic; tapping one picks it and plays a short example.
struct VoicePicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.pal) private var pal

    var body: some View {
        let isl = model.ui == .is
        VStack(alignment: .leading, spacing: 16) {
            if let list = model.voiceList {
                ForEach([("is", isl ? "Íslenska" : "Icelandic"), ("en", isl ? "Enska" : "English")], id: \.0) { lang, title in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(title.uppercased()).font(.caption2.weight(.semibold)).tracking(0.8).foregroundStyle(pal.text3)
                        ForEach(list.voices[lang] ?? []) { v in row(v, lang) }
                    }
                }
            } else {
                Label(isl ? "Raddþjónustan svarar ekki." : "The voice service is not answering.", systemImage: "speaker.slash")
                    .font(.footnote).foregroundStyle(pal.text3)
            }
        }
        .sensoryFeedback(.selection, trigger: model.voiceChoice)
    }

    private func row(_ v: VoiceOption, _ lang: String) -> some View {
        let chosen = model.activeVoice(lang) == v.id
        let key = "sample-\(v.id)"
        let playing = model.voice.speaking == key, loading = model.voice.loadingSpeech == key
        return Button { withAnimation(.spring(duration: 0.35, bounce: 0.25)) { model.tryVoice(v, language: lang) } } label: {
            HStack(spacing: 12) {
                ZStack {
                    Circle().fill(chosen ? pal.accent : pal.text.opacity(0.06))
                    if loading { ProgressView().controlSize(.mini).tint(chosen ? pal.accentInk : pal.accent) }
                    else {
                        Image(systemName: playing ? "speaker.wave.3.fill" : "play.fill")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(chosen ? pal.accentInk : pal.accent)
                            .symbolEffect(.variableColor.iterative, isActive: playing)
                            .contentTransition(.symbolEffect(.replace))
                    }
                }
                .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 1) {
                    Text(v.name).font(.subheadline.weight(chosen ? .semibold : .regular)).foregroundStyle(pal.text)
                    Text(v.engine).font(.caption2).foregroundStyle(pal.text3)
                }
                Spacer()
                if chosen {
                    Image(systemName: "checkmark").font(.subheadline.weight(.semibold)).foregroundStyle(pal.accent)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(chosen ? pal.soft : pal.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(chosen ? pal.accent.opacity(0.3) : pal.text.opacity(0.08)))
        }
        .buttonStyle(.plain)
    }
}
