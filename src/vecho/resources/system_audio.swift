// Captures everything the Mac plays (all apps) without any virtual audio driver, using a
// Core Audio process tap (macOS 14.4+). The tap sees audio *before* it reaches the output
// device, so it works with any speakers/headphones and follows output changes by itself,
// and it does not mute or alter what the user hears.
//
// Protocol on stdout: one text line "RATE <hz>\n", then raw little-endian Int16 mono PCM
// until the process is stopped (SIGINT/SIGTERM, or stdin closing when the parent exits).
// Diagnostics go to stderr; failures exit non-zero.

import AudioToolbox
import CoreAudio
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("error: " + message + "\n").utf8))
    exit(1)
}

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(
        mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
}

guard #available(macOS 14.2, *) else { fail("system audio capture needs macOS 14.4 or newer") }

let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
tapDescription.name = "vecho system audio"
tapDescription.isPrivate = true
tapDescription.muteBehavior = .unmuted

var tapID = AudioObjectID(kAudioObjectUnknown)
var status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
guard status == noErr else {
    fail("cannot create the audio tap (CoreAudio error \(status)); is system audio recording allowed?")
}

var formatAddress = address(kAudioTapPropertyFormat)
var format = AudioStreamBasicDescription()
var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
status = AudioObjectGetPropertyData(tapID, &formatAddress, 0, nil, &formatSize, &format)
guard status == noErr else { fail("cannot read the tap format (error \(status))") }
guard format.mFormatID == kAudioFormatLinearPCM,
    format.mFormatFlags & kAudioFormatFlagIsFloat != 0, format.mBitsPerChannel == 32
else { fail("unexpected tap sample format") }
let nonInterleaved = format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0

let aggregate: [String: Any] = [
    kAudioAggregateDeviceNameKey: "vecho-tap",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceIsPrivateKey: true,  // never shows up in the sound settings
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceTapListKey: [
        [
            kAudioSubTapUIDKey: tapDescription.uuid.uuidString,
            kAudioSubTapDriftCompensationKey: true,
        ]
    ],
]
var aggregateID = AudioObjectID(kAudioObjectUnknown)
status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
guard status == noErr else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("cannot create the capture device (CoreAudio error \(status))")
}

let out = FileHandle.standardOutput
out.write(Data("RATE \(Int(format.mSampleRate))\n".utf8))

let queue = DispatchQueue(label: "vecho.audio")
var procID: AudioDeviceIOProcID?
status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) {
    _, inputData, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
    guard let first = buffers.first, let firstData = first.mData else { return }

    let channels = nonInterleaved ? buffers.count : Int(first.mNumberChannels)
    let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size / (nonInterleaved ? 1 : max(channels, 1))
    guard frames > 0, channels > 0 else { return }

    var pcm = [Int16](repeating: 0, count: frames)
    for frame in 0..<frames {
        var sum: Float = 0
        if nonInterleaved {
            for channel in 0..<channels {
                if let data = buffers[channel].mData {
                    sum += data.assumingMemoryBound(to: Float.self)[frame]
                }
            }
        } else {
            let samples = firstData.assumingMemoryBound(to: Float.self)
            for channel in 0..<channels { sum += samples[frame * channels + channel] }
        }
        let mixed = max(-1, min(1, sum / Float(channels)))
        pcm[frame] = Int16(mixed * 32767)
    }
    pcm.withUnsafeBytes { out.write(Data($0)) }
}
guard status == noErr else { fail("cannot attach the audio callback (error \(status))") }
status = AudioDeviceStart(aggregateID, procID)
guard status == noErr else { fail("cannot start capturing (error \(status))") }

func shutDown() -> Never {
    AudioDeviceStop(aggregateID, procID)
    if let procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    exit(0)
}

var signalSources: [DispatchSourceSignal] = []
for signalNumber in [SIGINT, SIGTERM] {
    signal(signalNumber, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
    source.setEventHandler { shutDown() }
    source.resume()
    signalSources.append(source)
}
// The parent closing our stdin (or dying) also means: stop.
DispatchQueue.global().async {
    while FileHandle.standardInput.availableData.count > 0 {}
    DispatchQueue.main.async { shutDown() }
}
dispatchMain()
