import Foundation

/// A partition on a physical disk, as reported by `diskutil list -plist`.
public struct Partition: Equatable, Sendable {
    public var identifier: String  // e.g. disk4s1
    public var sizeBytes: Int64
    public var content: String?  // e.g. "DOS_FAT_32", "Apple_APFS"
    public var volumeName: String?
    public var mountPoint: String?

    public init(identifier: String, sizeBytes: Int64, content: String? = nil, volumeName: String? = nil, mountPoint: String? = nil) {
        self.identifier = identifier
        self.sizeBytes = sizeBytes
        self.content = content
        self.volumeName = volumeName
        self.mountPoint = mountPoint
    }
}

/// A candidate physical whole disk (never an APFS container or a disk image).
public struct Disk: Equatable, Identifiable, Sendable {
    public var identifier: String  // e.g. disk4
    public var sizeBytes: Int64
    public var mediaName: String?
    public var busProtocol: String?  // "USB", "Secure Digital", "Apple Fabric", ...
    public var removable: Bool  // diskutil RemovableMedia, the macOS analogue of lsblk RM
    public var isInternal: Bool
    public var writable: Bool
    public var partitions: [Partition]
    /// Every mount point backed by this disk, including APFS volumes (and the
    /// sealed "/" snapshot) in containers whose physical store lives here.
    public var mountPoints: [String]
    /// True if an APFS container flagged OSInternal (Preboot/Recovery/iSC) lives on this disk.
    public var hostsSystemContainer: Bool

    public init(
        identifier: String,
        sizeBytes: Int64,
        mediaName: String? = nil,
        busProtocol: String? = nil,
        removable: Bool,
        isInternal: Bool = false,
        writable: Bool = true,
        partitions: [Partition] = [],
        mountPoints: [String] = [],
        hostsSystemContainer: Bool = false
    ) {
        self.identifier = identifier
        self.sizeBytes = sizeBytes
        self.mediaName = mediaName
        self.busProtocol = busProtocol
        self.removable = removable
        self.isInternal = isInternal
        self.writable = writable
        self.partitions = partitions
        self.mountPoints = mountPoints
        self.hostsSystemContainer = hostsSystemContainer
    }

    public var id: String { identifier }
    public var deviceNode: String { "/dev/\(identifier)" }
    public var sizeHuman: String { humanSize(sizeBytes) }
    public var hasMountedVolumes: Bool { !mountPoints.isEmpty }

    public var displayModel: String {
        let name = (mediaName ?? "").trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? "Unknown device" : name
    }
}

/// Binary units with GB/TB-style labels, matching core/models.py:human_size.
public func humanSize(_ bytes: Int64) -> String {
    var value = Double(bytes)
    let units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units {
        if value < 1024.0 || unit == "PB" {
            return unit == "B" ? "\(Int64(value)) B" : String(format: "%.1f %@", value, unit)
        }
        value /= 1024.0
    }
    return String(format: "%.1f PB", value)
}
