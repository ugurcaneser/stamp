import Foundation
@testable import StampCore

/// Canned command output keyed by "executable arg1 arg2 …", with every call recorded.
final class FakeRunner: CommandRunning {
    var responses: [String: [CommandResult]] = [:]
    var calls: [[String]] = []

    func on(_ command: String, _ results: CommandResult...) {
        responses[command, default: []].append(contentsOf: results)
    }

    func run(_ executable: String, _ arguments: [String]) throws -> CommandResult {
        let key = ([executable] + arguments).joined(separator: " ")
        calls.append([executable] + arguments)
        guard var queue = responses[key], !queue.isEmpty else {
            return CommandResult(status: 99, stderr: "no fake response for: \(key)")
        }
        let result = queue.removeFirst()
        // Keep repeating the last response so polling callers don't run dry.
        responses[key] = queue.isEmpty ? [result] : queue
        return result
    }

    func runStreaming(_ executable: String, _ arguments: [String], onLine: (String) -> Void) throws -> CommandResult {
        let result = try run(executable, arguments)
        result.stdoutString.split(separator: "\n").forEach { onLine(String($0)) }
        return result
    }
}

func plistResult(_ object: [String: Any]) -> CommandResult {
    let data = try! PropertyListSerialization.data(fromPropertyList: object, format: .xml, options: 0)
    return CommandResult(status: 0, stdout: data)
}

/// The layout of a real Apple-silicon Mac (internal SSD with APFS
/// containers and the sealed "/" snapshot, a mounted disk image) plus a
/// 64GB USB SD card reader at disk4.
enum Fixtures {
    static let list: [String: Any] = [
        "AllDisksAndPartitions": [
            [
                "DeviceIdentifier": "disk0", "Size": 500_277_792_768, "Content": "GUID_partition_scheme", "OSInternal": false,
                "Partitions": [
                    ["DeviceIdentifier": "disk0s1", "Size": 524_288_000, "Content": "Apple_APFS_ISC"],
                    ["DeviceIdentifier": "disk0s2", "Size": 494_384_795_648, "Content": "Apple_APFS"],
                    ["DeviceIdentifier": "disk0s3", "Size": 5_368_664_064, "Content": "Apple_APFS_Recovery"],
                ],
            ],
            [
                "DeviceIdentifier": "disk1", "Size": 524_288_000, "Content": "Apple_APFS_Container", "OSInternal": true,
                "APFSPhysicalStores": [["DeviceIdentifier": "disk0s1"]],
                "APFSVolumes": [
                    ["DeviceIdentifier": "disk1s1", "MountPoint": "/System/Volumes/iSCPreboot", "VolumeName": "iSCPreboot", "MountedSnapshots": [Any]()],
                ],
            ],
            [
                "DeviceIdentifier": "disk3", "Size": 494_384_795_648, "Content": "Apple_APFS_Container", "OSInternal": false,
                "APFSPhysicalStores": [["DeviceIdentifier": "disk0s2"]],
                "APFSVolumes": [
                    ["DeviceIdentifier": "disk3s1", "MountPoint": "/System/Volumes/Data", "VolumeName": "Macintosh HD - Data", "MountedSnapshots": [Any]()],
                    ["DeviceIdentifier": "disk3s3", "VolumeName": "Macintosh HD",
                     "MountedSnapshots": [["SnapshotMountPoint": "/", "SnapshotBSD": "disk3s3s1"]]],
                ],
            ],
            [
                "DeviceIdentifier": "disk4", "Size": 63_864_569_856, "Content": "FDisk_partition_scheme", "OSInternal": false,
                "Partitions": [
                    ["DeviceIdentifier": "disk4s1", "Size": 63_863_521_280, "Content": "Windows_NTFS", "VolumeName": "OLDCARD", "MountPoint": "/Volumes/OLDCARD"],
                ],
            ],
            [
                "DeviceIdentifier": "disk7", "Size": 18_559_829_504, "Content": "GUID_partition_scheme", "OSInternal": false,
                "Partitions": [["DeviceIdentifier": "disk7s1", "Size": 18_559_795_200, "Content": "Apple_APFS"]],
            ],
        ],
    ]

    static let internalSSD: [String: Any] = [
        "DeviceIdentifier": "disk0", "MediaName": "APPLE SSD AP0512R", "BusProtocol": "Apple Fabric",
        "Internal": true, "Removable": false, "RemovableMedia": false, "WritableMedia": true,
        "VirtualOrPhysical": "Unknown", "TotalSize": 500_277_792_768,
    ]

    static let sdCard: [String: Any] = [
        "DeviceIdentifier": "disk4", "MediaName": "SD Card Reader", "BusProtocol": "USB",
        "Internal": false, "Removable": true, "RemovableMedia": true, "WritableMedia": true,
        "VirtualOrPhysical": "Physical", "TotalSize": 63_864_569_856,
    ]

    static let diskImage: [String: Any] = [
        "DeviceIdentifier": "disk7", "MediaName": "Disk Image", "BusProtocol": "Disk Image",
        "Internal": false, "Removable": true, "RemovableMedia": true, "WritableMedia": false,
        "VirtualOrPhysical": "Virtual", "TotalSize": 18_559_829_504,
    ]

    static func scanningRunner() -> FakeRunner {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil list -plist", plistResult(list))
        runner.on("/usr/sbin/diskutil info -plist disk0", plistResult(internalSSD))
        runner.on("/usr/sbin/diskutil info -plist disk4", plistResult(sdCard))
        runner.on("/usr/sbin/diskutil info -plist disk7", plistResult(diskImage))
        return runner
    }

    static func card(removable: Bool = true) -> Disk {
        Disk(identifier: "disk4", sizeBytes: 63_864_569_856, mediaName: "SD Card Reader", busProtocol: "USB", removable: removable)
    }
}

func makeTempDir() -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("stamp-tests-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// Builds a real zip with /usr/bin/zip from `files` (relative path -> contents).
func makeZip(_ files: [String: String], in dir: URL) -> URL {
    let src = dir.appendingPathComponent("zip-src")
    for (path, contents) in files {
        let url = src.appendingPathComponent(path)
        try! FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try! contents.write(to: url, atomically: true, encoding: .utf8)
    }
    let zip = dir.appendingPathComponent("test.zip")
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/zip")
    process.arguments = ["-qr", zip.path, "."]
    process.currentDirectoryURL = src
    try! process.run()
    process.waitUntilExit()
    return zip
}
