// The executable of vecho.app. It starts vecho (a Python program) as a child process and
// stays alive while it runs. macOS asks for microphone and system audio permission on behalf
// of the app a process was started from, so with this launcher the request says "vecho".
// A shell script that execs Python would make macOS ask for "python3.12" instead.
//
// Contents/Resources/command holds what to run: the executable on the first line, then one
// argument per line. Output goes to ~/.vecho/app.log.

import Foundation

let resources = Bundle.main.resourcePath ?? ""
guard let text = try? String(contentsOfFile: resources + "/command", encoding: .utf8) else {
    FileHandle.standardError.write(Data("vecho: missing Contents/Resources/command\n".utf8))
    exit(1)
}
let command = text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
guard let executable = command.first else { exit(1) }

let logDirectory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".vecho")
try? FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
let logPath = logDirectory.appendingPathComponent("app.log").path
if !FileManager.default.fileExists(atPath: logPath) {
    FileManager.default.createFile(atPath: logPath, contents: nil)
}
let log = FileHandle(forWritingAtPath: logPath)
log?.seekToEndOfFile()

let child = Process()
child.executableURL = URL(fileURLWithPath: executable)
child.arguments = Array(command.dropFirst())
if let log {
    child.standardOutput = log
    child.standardError = log
}
child.terminationHandler = { finished in exit(finished.terminationStatus) }

// Quitting the launcher (logging out, `kill`) asks vecho to quit, which saves a recording.
var sources: [DispatchSourceSignal] = []
for signalNumber in [SIGTERM, SIGINT, SIGHUP] {
    signal(signalNumber, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
    source.setEventHandler { kill(child.processIdentifier, SIGTERM) }
    source.resume()
    sources.append(source)
}

do {
    try child.run()
} catch {
    log?.write(Data("vecho: cannot start \(executable): \(error)\n".utf8))
    exit(1)
}
dispatchMain()
