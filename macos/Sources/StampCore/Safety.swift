import Foundation

public struct SafetyCheck: Equatable, Sendable {
    public var ok: Bool
    public var blockingReasons: [String]
    public var warnings: [String]
    public var requiresOverride: Bool
}

public enum SafetyError: Error, LocalizedError {
    case refused(String)

    public var errorDescription: String? {
        switch self {
        case .refused(let reasons): return reasons
        }
    }
}

/// Same rules as core/safety.py: the system disk and disks with system mounts
/// are blocked with no override; non-removable disks need an explicit override.
public enum Safety {
    /// Mount points (or prefixes) that mark a disk as carrying the running OS.
    public static let protectedMountPrefixes = [
        "/System", "/private", "/Library", "/Users", "/Applications", "/usr", "/opt", "/bin", "/sbin", "/cores",
    ]

    public static func isProtected(mountPoint: String) -> Bool {
        if mountPoint == "/" { return true }
        return protectedMountPrefixes.contains { mountPoint == $0 || mountPoint.hasPrefix($0 + "/") }
    }

    public static func backsRunningSystem(_ disk: Disk) -> Bool {
        disk.mountPoints.contains("/")
    }

    public static func hasProtectedMount(_ disk: Disk) -> Bool {
        disk.hostsSystemContainer || disk.mountPoints.contains(where: isProtected(mountPoint:))
    }

    public static func evaluate(_ disk: Disk, allowNonRemovable: Bool = false) -> SafetyCheck {
        var blocking: [String] = []
        var warnings: [String] = []
        var requiresOverride = false

        if backsRunningSystem(disk) {
            blocking.append("\(disk.deviceNode) is the disk backing the running operating system ('/') and cannot be selected.")
        }
        if hasProtectedMount(disk) {
            blocking.append("\(disk.deviceNode) holds a system volume (e.g. /System/Volumes/…) and cannot be selected.")
        }
        if !disk.removable {
            if allowNonRemovable {
                warnings.append("\(disk.deviceNode) is not flagged as removable. Proceeding because you explicitly overrode this check.")
            } else {
                blocking.append("\(disk.deviceNode) is not flagged as removable.")
                requiresOverride = true
            }
        }
        if !disk.writable {
            blocking.append("\(disk.deviceNode) is read-only and cannot be formatted.")
        }
        return SafetyCheck(ok: blocking.isEmpty, blockingReasons: blocking, warnings: warnings, requiresOverride: requiresOverride)
    }

    /// Disks fit to even list in the picker. Non-removable disks stay listed so
    /// the user can see them and consciously override.
    public static func candidates(_ disks: [Disk]) -> [Disk] {
        disks.filter { !backsRunningSystem($0) && !hasProtectedMount($0) }
    }
}
