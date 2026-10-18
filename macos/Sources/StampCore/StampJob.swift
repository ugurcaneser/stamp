import Darwin
import Foundation

public enum Phase: Int, CaseIterable, Sendable {
    case checkingZip, extracting, formatting, mounting, copying, finishing

    public var title: String {
        switch self {
        case .checkingZip: return "Check zip"
        case .extracting: return "Extract zip"
        case .formatting: return "Format as FAT32"
        case .mounting: return "Mount"
        case .copying: return "Copy files"
        case .finishing: return "Verify and eject"
        }
    }
}

public struct JobRequest: Sendable {
    public var disk: Disk  // as the user saw it when confirming
    public var zip: URL
    public var label: String
    public var allowNonRemovable: Bool

    public init(disk: Disk, zip: URL, label: String, allowNonRemovable: Bool) {
        self.disk = disk
        self.zip = zip
        self.label = label
        self.allowNonRemovable = allowNonRemovable
    }
}

public struct JobResult: Sendable {
    public var deviceNode: String
    public var label: String
    public var skipped: [String]
    public var ejected: Bool
}

public enum JobError: Error, LocalizedError {
    case diskChanged(String)
    case verificationFailed([String])

    public var errorDescription: String? {
        switch self {
        case .diskChanged(let detail): return detail
        case .verificationFailed(let missing): return "verification failed — missing after copy: \(missing.joined(separator: ", "))"
        }
    }
}

/// The whole format-and-copy flow, shared by the GUI and the tests.
/// Ordering differs from the Linux worker on purpose: the zip is checked and
/// extracted before the card is touched, so a bad zip never costs a wipe.
public struct StampJob {
    public typealias Reporter = (_ phase: Phase, _ fraction: Double?, _ message: String?) -> Void

    private let scanner: DiskScanner
    private let formatter: DiskFormatter
    private let writer: ContentWriter
    private let flush: () -> Void

    public init(
        scanner: DiskScanner = DiskScanner(),
        formatter: DiskFormatter = DiskFormatter(),
        writer: ContentWriter = ContentWriter(),
        flush: @escaping () -> Void = { Darwin.sync() }
    ) {
        self.scanner = scanner
        self.formatter = formatter
        self.writer = writer
        self.flush = flush
    }

    public func run(_ request: JobRequest, report: Reporter) throws -> JobResult {
        let label = DiskFormatter.sanitizeLabel(request.label)
        StampLog.info("job requested: device=\(request.disk.deviceNode) size=\(request.disk.sizeBytes) model=\(request.disk.displayModel) zip=\(request.zip.path) label=\(label) override=\(request.allowNonRemovable)")

        report(.checkingZip, nil, "Checking \(request.zip.lastPathComponent)…")
        let preview = try writer.preview(zip: request.zip)

        let workDir = FileManager.default.temporaryDirectory.appendingPathComponent("stamp-extract-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: workDir) }
        report(.extracting, 0, "Extracting \(preview.fileCount) files…")
        try writer.extract(zip: request.zip, to: workDir, fileCount: preview.fileCount) { report(.extracting, $0, nil) }

        // Re-validate against a fresh scan: time has passed since the user
        // confirmed, and the card may have been swapped for another disk.
        report(.formatting, nil, "Re-checking \(request.disk.deviceNode)…")
        guard let disk = try scanner.disk(identifier: request.disk.identifier) else {
            throw JobError.diskChanged("\(request.disk.deviceNode) is no longer attached")
        }
        guard disk.sizeBytes == request.disk.sizeBytes, disk.displayModel == request.disk.displayModel else {
            throw JobError.diskChanged("\(request.disk.deviceNode) changed since it was selected (now \(disk.displayModel), \(disk.sizeHuman)); nothing was touched")
        }
        let check = Safety.evaluate(disk, allowNonRemovable: request.allowNonRemovable)
        guard check.ok else {
            StampLog.warning("refusing to format \(disk.deviceNode): \(check.blockingReasons.joined(separator: "; "))")
            throw SafetyError.refused(check.blockingReasons.joined(separator: " "))
        }

        report(.formatting, nil, "Creating a fresh MBR partition table on \(disk.deviceNode) and formatting as FAT32…")
        try formatter.erase(disk, label: label)

        report(.mounting, nil, nil)
        let partition = DiskFormatter.partitionIdentifier(for: disk)
        let mountPoint = URL(fileURLWithPath: try formatter.mountPoint(ofPartition: partition))
        report(.mounting, nil, "\(partition) is FAT32, mounted at \(mountPoint.path).")

        report(.copying, 0, "Copying files…")
        let skipped = try writer.copyTree(from: workDir, to: mountPoint) { report(.copying, $0, nil) }

        report(.finishing, nil, "Flushing writes to the card…")
        writer.removeMacMetadata(volume: mountPoint, source: workDir)
        flush()
        let missing = try writer.missingTopLevel(at: mountPoint, expected: preview.topLevelNames)
        guard missing.isEmpty else {
            StampLog.error("verification failed on \(mountPoint.path): missing \(missing)")
            throw JobError.verificationFailed(missing)
        }

        var ejected = true
        do {
            try formatter.eject(disk)
        } catch {
            ejected = false
            StampLog.warning("\(error.localizedDescription)")
            report(.finishing, nil, "Warning: \(error.localizedDescription)")
        }
        StampLog.info("job complete: \(disk.deviceNode) is FAT32 (label \(label)) and loaded")
        return JobResult(deviceNode: disk.deviceNode, label: label, skipped: skipped, ejected: ejected)
    }
}
