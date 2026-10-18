import Foundation

public enum ScanError: Error, LocalizedError {
    case commandFailed(String)
    case badOutput(String)

    public var errorDescription: String? {
        switch self {
        case .commandFailed(let detail): return "diskutil failed: \(detail)"
        case .badOutput(let detail): return "could not parse diskutil output: \(detail)"
        }
    }
}

/// Enumerates physical whole disks via `diskutil list -plist` + `diskutil info -plist`,
/// the macOS counterpart of core/device_scanner.py's lsblk parsing.
public struct DiskScanner {
    public static let diskutil = "/usr/sbin/diskutil"

    private let runner: CommandRunning

    public init(runner: CommandRunning = ProcessRunner()) {
        self.runner = runner
    }

    public func scan() throws -> [Disk] {
        let list = try plist(["list", "-plist"])
        var infos: [String: [String: Any]] = [:]
        for identifier in DiskScanner.wholeDiskCandidates(list) {
            infos[identifier] = try plist(["info", "-plist", identifier])
        }
        return DiskScanner.buildDisks(list: list, infos: infos)
    }

    public func disk(identifier: String) throws -> Disk? {
        try scan().first { $0.identifier == identifier }
    }

    private func plist(_ arguments: [String]) throws -> [String: Any] {
        let result = try runner.run(DiskScanner.diskutil, arguments)
        guard result.status == 0 else {
            throw ScanError.commandFailed("diskutil \(arguments.joined(separator: " ")): \(result.detail)")
        }
        return try DiskScanner.parsePlist(result.stdout)
    }

    static func parsePlist(_ data: Data) throws -> [String: Any] {
        do {
            guard let dict = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else {
                throw ScanError.badOutput("top level is not a dictionary")
            }
            return dict
        } catch let error as ScanError {
            throw error
        } catch {
            throw ScanError.badOutput(error.localizedDescription)
        }
    }

    private static func entries(_ list: [String: Any]) -> [[String: Any]] {
        list["AllDisksAndPartitions"] as? [[String: Any]] ?? []
    }

    /// Whole disks that aren't APFS containers: containers are synthesized
    /// on top of a physical store, not media you could format.
    static func wholeDiskCandidates(_ list: [String: Any]) -> [String] {
        entries(list)
            .filter { $0["APFSPhysicalStores"] == nil }
            .compactMap { $0["DeviceIdentifier"] as? String }
    }

    static func buildDisks(list: [String: Any], infos: [String: [String: Any]]) -> [Disk] {
        let all = entries(list)
        var parentOf: [String: String] = [:]
        var mounts: [String: [String]] = [:]
        var partitionsOf: [String: [Partition]] = [:]
        var systemContainerOwners: Set<String> = []

        for entry in all where entry["APFSPhysicalStores"] == nil {
            guard let identifier = entry["DeviceIdentifier"] as? String else { continue }
            parentOf[identifier] = identifier
            if let mountPoint = entry["MountPoint"] as? String {
                mounts[identifier, default: []].append(mountPoint)
            }
            for raw in entry["Partitions"] as? [[String: Any]] ?? [] {
                guard let partitionID = raw["DeviceIdentifier"] as? String else { continue }
                parentOf[partitionID] = identifier
                let mountPoint = raw["MountPoint"] as? String
                if let mountPoint {
                    mounts[identifier, default: []].append(mountPoint)
                }
                partitionsOf[identifier, default: []].append(Partition(
                    identifier: partitionID,
                    sizeBytes: int64(raw["Size"]) ?? 0,
                    content: raw["Content"] as? String,
                    volumeName: raw["VolumeName"] as? String,
                    mountPoint: mountPoint
                ))
            }
        }

        // Attribute APFS volumes (including the sealed "/" snapshot) back to
        // the physical disk(s) their container lives on, so the boot disk
        // shows up as backing "/" even though "/" is on a synthesized disk.
        for entry in all {
            guard let stores = entry["APFSPhysicalStores"] as? [[String: Any]] else { continue }
            let owners = Set(stores.compactMap { ($0["DeviceIdentifier"] as? String).flatMap { parentOf[$0] } })
            var volumeMounts: [String] = []
            for volume in entry["APFSVolumes"] as? [[String: Any]] ?? [] {
                if let mountPoint = volume["MountPoint"] as? String {
                    volumeMounts.append(mountPoint)
                }
                for snapshot in volume["MountedSnapshots"] as? [[String: Any]] ?? [] {
                    if let mountPoint = snapshot["SnapshotMountPoint"] as? String {
                        volumeMounts.append(mountPoint)
                    }
                }
            }
            let osInternal = entry["OSInternal"] as? Bool ?? false
            for owner in owners {
                mounts[owner, default: []].append(contentsOf: volumeMounts)
                if osInternal { systemContainerOwners.insert(owner) }
            }
        }

        var disks: [Disk] = []
        for entry in all where entry["APFSPhysicalStores"] == nil {
            guard let identifier = entry["DeviceIdentifier"] as? String, let info = infos[identifier] else { continue }
            // Disk images (mounted .dmg, simulator runtimes) report RemovableMedia
            // too; they are never something to format.
            if info["VirtualOrPhysical"] as? String == "Virtual" || info["BusProtocol"] as? String == "Disk Image" {
                continue
            }
            disks.append(Disk(
                identifier: identifier,
                sizeBytes: int64(info["TotalSize"]) ?? int64(info["Size"]) ?? int64(entry["Size"]) ?? 0,
                mediaName: info["MediaName"] as? String,
                busProtocol: info["BusProtocol"] as? String,
                removable: info["RemovableMedia"] as? Bool ?? info["Removable"] as? Bool ?? false,
                isInternal: info["Internal"] as? Bool ?? false,
                writable: info["WritableMedia"] as? Bool ?? true,
                partitions: partitionsOf[identifier] ?? [],
                mountPoints: mounts[identifier] ?? [],
                hostsSystemContainer: systemContainerOwners.contains(identifier)
            ))
        }
        return disks.sorted { diskNumber($0.identifier) < diskNumber($1.identifier) }
    }

    private static func diskNumber(_ identifier: String) -> Int {
        Int(identifier.dropFirst("disk".count)) ?? Int.max
    }
}

func int64(_ value: Any?) -> Int64? {
    (value as? NSNumber)?.int64Value
}
