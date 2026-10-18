import Foundation

public enum FormatError: Error, LocalizedError {
    case invalidIdentifier(String)
    case commandFailed(String)
    case notFAT32(String)
    case mountFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidIdentifier(let id): return "refusing to format \(id): does not look like a whole-disk identifier"
        case .commandFailed(let detail): return detail
        case .notFAT32(let detail): return detail
        case .mountFailed(let detail): return detail
        }
    }
}

/// Partition + forced FAT32 via `diskutil eraseDisk FAT32 … MBRFormat`.
///
/// Unlike the Linux build there is no privileged helper: diskutil's "FAT32"
/// personality is MS-DOS FAT32 (newfs_msdos -F 32) regardless of card size,
/// and diskutil erases external disks for the logged-in user without an
/// admin password, so nothing here runs as root.
public struct DiskFormatter {
    public static let defaultLabel = "STAMP"

    private let runner: CommandRunning
    private let sleep: (TimeInterval) -> Void

    public init(runner: CommandRunning = ProcessRunner(), sleep: @escaping (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) }) {
        self.runner = runner
        self.sleep = sleep
    }

    /// FAT32 volume labels: up to 11 chars, uppercase, ASCII letters/digits/space/_/-.
    /// Same result as core/formatter.py:sanitize_label after stamp-helper's own filter.
    public static func sanitizeLabel(_ label: String?) -> String {
        let trimmed = (label ?? "").trimmingCharacters(in: .whitespaces)
        let allowed = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_ ")
        let cleaned = String(trimmed.filter { allowed.contains($0) }).trimmingCharacters(in: .whitespaces)
        let truncated = String(cleaned.prefix(11)).trimmingCharacters(in: .whitespaces)
        return truncated.isEmpty ? defaultLabel : truncated.uppercased()
    }

    public static func isWholeDiskIdentifier(_ identifier: String) -> Bool {
        identifier.range(of: #"^disk[0-9]+$"#, options: .regularExpression) != nil
    }

    /// MBRFormat with one partition always yields <disk>s1.
    public static func partitionIdentifier(for disk: Disk) -> String {
        "\(disk.identifier)s1"
    }

    public func erase(_ disk: Disk, label: String) throws {
        guard DiskFormatter.isWholeDiskIdentifier(disk.identifier) else {
            throw FormatError.invalidIdentifier(disk.identifier)
        }
        let safeLabel = DiskFormatter.sanitizeLabel(label)
        let arguments = ["eraseDisk", "FAT32", safeLabel, "MBRFormat", disk.deviceNode]
        StampLog.info("running: diskutil \(arguments.joined(separator: " "))")
        let result = try runner.run(DiskScanner.diskutil, arguments)
        guard result.status == 0 else {
            StampLog.error("diskutil eraseDisk failed for \(disk.deviceNode) (exit \(result.status)): \(result.detail)")
            throw FormatError.commandFailed("formatting \(disk.deviceNode) failed: \(result.detail)")
        }
        StampLog.info("diskutil eraseDisk succeeded for \(disk.deviceNode)")
    }

    /// Mount point of the freshly created partition, mounting it if diskutil
    /// didn't already, after checking it really is FAT32.
    public func mountPoint(ofPartition identifier: String) throws -> String {
        var info = try partitionInfo(identifier)
        try checkFAT32(identifier, info)
        if let mountPoint = nonEmpty(info["MountPoint"]) {
            return mountPoint
        }
        let result = try runner.run(DiskScanner.diskutil, ["mount", identifier])
        guard result.status == 0 else {
            throw FormatError.mountFailed("failed to mount \(identifier): \(result.detail)")
        }
        info = try partitionInfo(identifier)
        guard let mountPoint = nonEmpty(info["MountPoint"]) else {
            throw FormatError.mountFailed("\(identifier) reported no mount point after mounting")
        }
        return mountPoint
    }

    public func eject(_ disk: Disk) throws {
        let result = try runner.run(DiskScanner.diskutil, ["eject", disk.deviceNode])
        guard result.status == 0 else {
            throw FormatError.commandFailed("failed to eject \(disk.deviceNode): \(result.detail)")
        }
        StampLog.info("ejected \(disk.deviceNode)")
    }

    /// The partition node can take a moment to show up after eraseDisk returns.
    private func partitionInfo(_ identifier: String, attempts: Int = 20) throws -> [String: Any] {
        var lastDetail = ""
        for attempt in 0..<attempts {
            let result = try runner.run(DiskScanner.diskutil, ["info", "-plist", identifier])
            if result.status == 0 {
                return try DiskScanner.parsePlist(result.stdout)
            }
            lastDetail = result.detail
            if attempt < attempts - 1 { sleep(0.25) }
        }
        throw FormatError.mountFailed("\(identifier) never appeared (partition creation may have failed): \(lastDetail)")
    }

    private func checkFAT32(_ identifier: String, _ info: [String: Any]) throws {
        let type = info["FilesystemType"] as? String
        let name = info["FilesystemName"] as? String ?? info["FilesystemUserVisibleName"] as? String
        if let type, type != "msdos" {
            throw FormatError.notFAT32("\(identifier) has filesystem \(type), expected FAT32")
        }
        if let name, !name.contains("FAT32") {
            throw FormatError.notFAT32("\(identifier) was formatted as \(name), not FAT32")
        }
    }

    private func nonEmpty(_ value: Any?) -> String? {
        guard let string = value as? String, !string.isEmpty else { return nil }
        return string
    }
}
