import Foundation
import os

/// Appends every device/zip choice, command and outcome to
/// ~/Library/Logs/Stamp/stamp.log (the macOS counterpart of
/// ~/.local/state/stamp/stamp.log), and mirrors it to the unified log.
public enum StampLog {
    public static var isEnabled = true
    public static let fileURL: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Stamp/stamp.log")

    private static let logger = Logger(subsystem: "com.ugurcaneser.stamp", category: "stamp")
    private static let lock = NSLock()

    public static func info(_ message: String) { write("INFO", message) }
    public static func warning(_ message: String) { write("WARNING", message) }
    public static func error(_ message: String) { write("ERROR", message) }

    private static func write(_ level: String, _ message: String) {
        guard isEnabled else { return }
        logger.log("\(level, privacy: .public) \(message, privacy: .public)")
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(level) \(message)\n"
        lock.lock()
        defer { lock.unlock() }
        let fm = FileManager.default
        try? fm.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: fileURL.path) {
            fm.createFile(atPath: fileURL.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: fileURL) {
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
            try? handle.close()
        }
    }
}
