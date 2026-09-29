// Creates (or removes) a stacked "Multi-Output" aggregate device that plays to the real speakers
// and to a loopback device (BlackHole) at the same time, so vecho can hear system audio.
//
//   swift multi_output.swift list
//   swift multi_output.swift describe [--name NAME]
//   swift multi_output.swift create [--name NAME] [--output DEVICE_NAME] [--loopback SUBSTRING]
//   swift multi_output.swift destroy [--name NAME]
//
// Every line printed to stdout is machine-readable; failures exit non-zero with a message on stderr.

import CoreAudio
import Foundation

let defaultName = "vecho Multi-Output"
let deviceUID = "com.vecho.multi-output"
let system = AudioObjectID(kAudioObjectSystemObject)

func address(
    _ selector: AudioObjectPropertySelector,
    _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal
) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(
        mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func allDevices() -> [AudioObjectID] {
    var addr = address(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func stringProperty(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
    var addr = address(selector)
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    var value: Unmanaged<CFString>?
    let status = withUnsafeMutablePointer(to: &value) {
        AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0)
    }
    guard status == noErr, let unmanaged = value else { return nil }
    return unmanaged.takeRetainedValue() as String
}

func channelCount(_ id: AudioObjectID, scope: AudioObjectPropertyScope) -> Int {
    var addr = address(kAudioDevicePropertyStreamConfiguration, scope)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else {
        return 0
    }
    let raw = UnsafeMutableRawPointer.allocate(
        byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}

struct Device {
    let id: AudioObjectID
    let name: String
    let uid: String
    let inputs: Int
    let outputs: Int
}

func devices() -> [Device] {
    allDevices().compactMap { id in
        guard let name = stringProperty(id, kAudioObjectPropertyName),
            let uid = stringProperty(id, kAudioDevicePropertyDeviceUID)
        else { return nil }
        return Device(
            id: id, name: name, uid: uid,
            inputs: channelCount(id, scope: kAudioObjectPropertyScopeInput),
            outputs: channelCount(id, scope: kAudioObjectPropertyScopeOutput))
    }
}

func defaultOutputDevice() -> Device? {
    var addr = address(kAudioHardwarePropertyDefaultOutputDevice)
    var id = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &id) == noErr else { return nil }
    return devices().first { $0.id == id }
}

func mainSubDeviceUID(_ id: AudioObjectID) -> String? {
    stringProperty(id, kAudioAggregateDevicePropertyMainSubDevice)
}

/// Sub-devices that are actually running (configured ones CoreAudio cannot use are dropped).
func activeSubDevices(_ id: AudioObjectID) -> [AudioObjectID] {
    var addr = address(kAudioAggregateDevicePropertyActiveSubDeviceList)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else {
        return []
    }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func option(_ name: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    return args[index + 1]
}

func destroy(_ device: Device) {
    let status = AudioHardwareDestroyAggregateDevice(device.id)
    if status != noErr { fail("cannot remove '\(device.name)' (CoreAudio error \(status))") }
}

func create(_ args: [String]) {
    let name = option("--name", in: args) ?? defaultName
    let loopbackHint = (option("--loopback", in: args) ?? "blackhole").lowercased()
    let all = devices()

    guard let loopback = all.first(where: { $0.name.lowercased().contains(loopbackHint) && $0.outputs > 0 })
    else { fail("no loopback output device matching '\(loopbackHint)' (is BlackHole installed?)") }

    let physical: Device?
    if let wanted = option("--output", in: args) {
        physical = all.first { $0.name == wanted && $0.outputs > 0 }
    } else {
        // Follow the device the user currently listens on, unless that is already virtual.
        let current = defaultOutputDevice()
        let virtualNow = current.map {
            $0.uid == deviceUID || $0.name.lowercased().contains(loopbackHint)
        } ?? true
        physical = virtualNow
            ? all.first { $0.outputs > 0 && $0.uid.contains("BuiltIn") } : current
    }
    guard let speakers = physical else { fail("no physical output device found; use --output NAME") }

    if let existing = all.first(where: { $0.uid == deviceUID }) { destroy(existing) }

    let description: [String: Any] = [
        kAudioAggregateDeviceNameKey: name,
        kAudioAggregateDeviceUIDKey: deviceUID,
        kAudioAggregateDeviceIsStackedKey: 1,  // stacked = Multi-Output (not a merged aggregate)
        kAudioAggregateDeviceMainSubDeviceKey: speakers.uid,
        kAudioAggregateDeviceSubDeviceListKey: [
            [kAudioSubDeviceUIDKey: speakers.uid],
            [kAudioSubDeviceUIDKey: loopback.uid, kAudioSubDeviceDriftCompensationKey: 1],
        ],
    ]
    var newID = AudioObjectID(0)
    let status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &newID)
    if status != noErr { fail("CoreAudio refused to create the device (error \(status))") }

    // CoreAudio accepts the configuration but silently deactivates sub-devices it cannot stack
    // (e.g. another aggregate device), which would leave the user with no sound. Verify that both
    // outputs are really running and the real output is the clock source, else undo.
    func healthy() -> Bool {
        let active = activeSubDevices(newID)
        return active.contains(speakers.id) && active.contains(loopback.id)
            && mainSubDeviceUID(newID) == speakers.uid
    }
    var ok = healthy()
    for _ in 0..<10 where !ok {
        Thread.sleep(forTimeInterval: 0.1)
        ok = healthy()
    }
    guard ok else {
        AudioHardwareDestroyAggregateDevice(newID)
        fail("'\(speakers.name)' cannot be combined with the loopback device (virtual or aggregate output?)")
    }
    print("created\t\(name)\t\(speakers.name)\t\(loopback.name)")
}

let args = Array(CommandLine.arguments.dropFirst())
switch args.first {
case "list":
    for device in devices() {
        print("\(device.name)\t\(device.uid)\t\(device.inputs)\t\(device.outputs)")
    }
case "describe":
    // Which physical device does the existing Multi-Output play through?
    let all = devices()
    if let multi = all.first(where: { $0.uid == deviceUID }),
        let uid = mainSubDeviceUID(multi.id),
        let main = all.first(where: { $0.uid == uid })
    {
        print("main\t\(main.name)\t\(main.uid)")
    } else {
        print("absent")
    }
case "create":
    create(args)
case "destroy":
    let name = option("--name", in: args) ?? defaultName
    guard let device = devices().first(where: { $0.uid == deviceUID || $0.name == name }) else {
        print("absent")
        exit(0)
    }
    destroy(device)
    print("destroyed\t\(device.name)")
default:
    fail("usage: multi_output.swift list | describe | create [--name N] [--output NAME] [--loopback HINT] | destroy [--name N]")
}
