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
var aggregateID = AudioObjectID(kAudioObjectUnknown)
var procID: AudioDeviceIOProcID?
var running = false

// Releases whatever was created so far; used on every way out.
func cleanUp() {
    if running { AudioDeviceStop(aggregateID, procID) }
    if let procID { AudioDeviceDestroyIOProcID(aggregateID, procID) }
    if aggregateID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggregateID) }
    if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
}

func abort(_ message: String) -> Never {
    cleanUp()
    fail(message)
}

// stdout carries the protocol. Writes go straight to the file descriptor so a closed pipe
// (the parent went away) is an error we handle, not an exception or a SIGPIPE crash.
signal(SIGPIPE, SIG_IGN)
func writeAll(_ data: Data) -> Bool {
    data.withUnsafeBytes { raw -> Bool in
        guard var pointer = raw.baseAddress else { return true }
        var left = raw.count
        while left > 0 {
            let written = write(STDOUT_FILENO, pointer, left)
            if written < 0 {
                if errno == EINTR { continue }
                return false
            }
            pointer += written
            left -= written
        }
        return true
    }
}

var status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
guard status == noErr else {
    tapID = kAudioObjectUnknown
    abort("cannot create the audio tap (CoreAudio error \(status)); is system audio recording allowed?")
}

var formatAddress = address(kAudioTapPropertyFormat)
var format = AudioStreamBasicDescription()
var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
status = AudioObjectGetPropertyData(tapID, &formatAddress, 0, nil, &formatSize, &format)
guard status == noErr else { abort("cannot read the tap format (error \(status))") }
guard format.mFormatID == kAudioFormatLinearPCM,
    format.mFormatFlags & kAudioFormatFlagIsFloat != 0, format.mBitsPerChannel == 32
else { abort("unexpected tap sample format") }
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
status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
guard status == noErr else {
    aggregateID = kAudioObjectUnknown
    abort("cannot create the capture device (CoreAudio error \(status))")
}

// The audio callback never waits on the pipe: if the parent is slow to read, a blocked
// callback makes Core Audio silently drop what it captured. Samples go into a buffer that
// holds a minute of audio, and a writer thread empties it. Should it ever fill up, the
// missing stretch is sent as silence later, so the track keeps its length and alignment.
let capacity = max(Int(format.mSampleRate), 1) * 60
let ring = UnsafeMutablePointer<Int16>.allocate(capacity: capacity)
var ringStart = 0  // oldest unsent sample
var ringCount = 0
var pendingSilence = 0  // samples lost to a full buffer, sent as zeros in their place
let ringLock = NSLock()
let ringReady = DispatchSemaphore(value: 0)

// Audio is only sent after the "RATE" line, which is only sent once capturing really started.
var announced = false
var outputBroken = false

func push(_ sample: Int16) {
    if pendingSilence > 0 || ringCount == capacity {
        pendingSilence += 1
        return
    }
    ring[(ringStart + ringCount) % capacity] = sample
    ringCount += 1
}

let queue = DispatchQueue(label: "vecho.audio")
status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) {
    _, inputData, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
    guard let first = buffers.first, let firstData = first.mData else { return }

    let channels = nonInterleaved ? buffers.count : Int(first.mNumberChannels)
    let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size / (nonInterleaved ? 1 : max(channels, 1))
    guard frames > 0, channels > 0 else { return }

    ringLock.lock()
    if !announced || outputBroken {
        ringLock.unlock()
        return
    }
    if pendingSilence > 0 && capacity - ringCount >= pendingSilence {
        for _ in 0..<pendingSilence {  // room again: the lost stretch goes in as silence
            ring[(ringStart + ringCount) % capacity] = 0
            ringCount += 1
        }
        pendingSilence = 0
    }
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
        push(Int16(mixed * 32767))
    }
    ringLock.unlock()
    ringReady.signal()
}
guard status == noErr else {
    procID = nil
    abort("cannot attach the audio callback (error \(status))")
}
status = AudioDeviceStart(aggregateID, procID)
guard status == noErr else { abort("cannot start capturing (error \(status))") }
running = true

if !writeAll(Data("RATE \(Int(format.mSampleRate))\n".utf8)) { abort("the parent process is gone") }
ringLock.lock()
announced = true
ringLock.unlock()

// Empties the buffer into the pipe; only this thread writes audio to stdout.
Thread.detachNewThread {
    let chunk = UnsafeMutablePointer<Int16>.allocate(capacity: capacity)
    while true {
        ringReady.wait()
        ringLock.lock()
        let count = ringCount
        for index in 0..<count { chunk[index] = ring[(ringStart + index) % capacity] }
        ringStart = (ringStart + count) % capacity
        ringCount = 0
        ringLock.unlock()
        if count == 0 { continue }
        let data = Data(bytesNoCopy: chunk, count: count * 2, deallocator: .none)
        if !writeAll(data) {
            ringLock.lock()
            outputBroken = true  // the parent is gone: stop cleanly
            ringLock.unlock()
            DispatchQueue.main.async { shutDown() }
            return
        }
    }
}

func shutDown() -> Never {
    cleanUp()
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
