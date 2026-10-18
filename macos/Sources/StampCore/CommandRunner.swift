import Foundation

public struct CommandResult: Sendable {
    public var status: Int32
    public var stdout: Data
    public var stderr: String

    public init(status: Int32, stdout: Data = Data(), stderr: String = "") {
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
    }

    public var stdoutString: String { String(decoding: stdout, as: UTF8.self) }

    /// stderr if it says anything, else stdout — for error messages.
    public var detail: String {
        let err = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        return err.isEmpty ? stdoutString.trimmingCharacters(in: .whitespacesAndNewlines) : err
    }
}

/// Runs external tools (diskutil, unzip). Injected everywhere so tests can
/// substitute canned output, like the CommandRunner callables in core/formatter.py.
public protocol CommandRunning {
    func run(_ executable: String, _ arguments: [String]) throws -> CommandResult
    /// Like run(), but hands each stdout line to `onLine` as it arrives.
    func runStreaming(_ executable: String, _ arguments: [String], onLine: (String) -> Void) throws -> CommandResult
}

public struct ProcessRunner: CommandRunning {
    public init() {}

    public func run(_ executable: String, _ arguments: [String]) throws -> CommandResult {
        var collected = Data()
        let result = try runStreamingData(executable, arguments) { collected.append($0) }
        return CommandResult(status: result.status, stdout: collected, stderr: result.stderr)
    }

    public func runStreaming(_ executable: String, _ arguments: [String], onLine: (String) -> Void) throws -> CommandResult {
        var collected = Data()
        var pending = Data()
        let result = try runStreamingData(executable, arguments) { chunk in
            collected.append(chunk)
            pending.append(chunk)
            while let newline = pending.firstIndex(of: UInt8(ascii: "\n")) {
                onLine(String(decoding: pending[pending.startIndex..<newline], as: UTF8.self))
                pending.removeSubrange(pending.startIndex...newline)
            }
        }
        if !pending.isEmpty {
            onLine(String(decoding: pending, as: UTF8.self))
        }
        return CommandResult(status: result.status, stdout: collected, stderr: result.stderr)
    }

    private final class Box: @unchecked Sendable {
        var data = Data()
    }

    private func runStreamingData(_ executable: String, _ arguments: [String], onChunk: (Data) -> Void) throws -> CommandResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        process.standardInput = FileHandle.nullDevice
        try process.run()

        // Drain stderr concurrently so a chatty tool can't fill the pipe and deadlock.
        let errBox = Box()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global().async {
            errBox.data = err.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        let reader = out.fileHandleForReading
        while true {
            let chunk = reader.availableData
            if chunk.isEmpty { break }
            onChunk(chunk)
        }
        group.wait()
        process.waitUntilExit()
        return CommandResult(status: process.terminationStatus, stderr: String(decoding: errBox.data, as: UTF8.self))
    }
}
