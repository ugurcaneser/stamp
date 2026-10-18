import Darwin
import Foundation

public enum ContentError: Error, LocalizedError {
    case invalidZip(String)
    case extractFailed(String)
    case copyFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidZip(let detail), .extractFailed(let detail), .copyFailed(let detail): return detail
        }
    }
}

public struct ZipPreview: Equatable, Sendable {
    public var fileCount: Int
    public var uncompressedBytes: Int64
    public var topLevelNames: [String]

    /// Warns (does not block) when the contents won't fit, like core/content_writer.py.
    public func warnings(targetCapacity: Int64?) -> [String] {
        guard let targetCapacity, uncompressedBytes > targetCapacity else { return [] }
        return ["Uncompressed contents (\(humanSize(uncompressedBytes))) exceed the target's capacity (\(humanSize(targetCapacity)))."]
    }
}

/// Zip validation/preview/extraction (via the system `unzip`) and the copy
/// onto the card. The copy is done here rather than with rsync/ditto/cp:
/// /usr/bin/rsync is openrsync without progress output, and copying data
/// only (no extended attributes) keeps macOS from littering the FAT volume
/// with AppleDouble "._" files.
public struct ContentWriter {
    public static let unzip = "/usr/bin/unzip"

    /// Folders macOS creates on a freshly mounted FAT volume.
    static let macMetadataNames = [".Spotlight-V100", ".Trashes", ".TemporaryItems", ".fseventsd"]

    private let runner: CommandRunning

    public init(runner: CommandRunning = ProcessRunner()) {
        self.runner = runner
    }

    public func validate(zip: URL) throws {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: zip.path, isDirectory: &isDirectory), !isDirectory.boolValue else {
            throw ContentError.invalidZip("\(zip.path) does not exist or is not a file")
        }
        let result = try runner.run(ContentWriter.unzip, ["-tqq", zip.path])
        guard result.status == 0 else {
            throw ContentError.invalidZip("\(zip.lastPathComponent) is not a valid zip file or is corrupt: \(firstLine(result.detail))")
        }
    }

    public func preview(zip: URL) throws -> ZipPreview {
        try validate(zip: zip)
        let result = try runner.run(ContentWriter.unzip, ["-l", zip.path])
        guard result.status == 0 else {
            throw ContentError.invalidZip("could not list \(zip.lastPathComponent): \(result.detail)")
        }
        let entries = ContentWriter.parseListing(result.stdoutString)
        let files = entries.filter { !$0.name.hasSuffix("/") }
        guard !files.isEmpty else {
            throw ContentError.invalidZip("\(zip.lastPathComponent) is empty")
        }
        let topLevel = Set(entries.compactMap { entry -> String? in
            let top = entry.name.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: true).first.map(String.init)
            return top == "." ? nil : top
        })
        return ZipPreview(
            fileCount: files.count,
            uncompressedBytes: files.reduce(0) { $0 + $1.size },
            topLevelNames: topLevel.sorted()
        )
    }

    /// Rows of `unzip -l` between its two dashed separator lines.
    static func parseListing(_ output: String) -> [(size: Int64, name: String)] {
        var rows: [(size: Int64, name: String)] = []
        var separators = 0
        let row = try! NSRegularExpression(pattern: #"^\s*(\d+)\s+\S+\s+\S+   (.*)$"#)
        for line in output.components(separatedBy: "\n") {
            if line.hasPrefix("---------") {
                separators += 1
                continue
            }
            guard separators == 1 else { continue }
            let range = NSRange(line.startIndex..., in: line)
            guard let match = row.firstMatch(in: line, range: range),
                  let sizeRange = Range(match.range(at: 1), in: line),
                  let nameRange = Range(match.range(at: 2), in: line) else { continue }
            rows.append((Int64(line[sizeRange]) ?? 0, String(line[nameRange])))
        }
        return rows
    }

    /// Extracts into `destination`, reporting progress by files extracted.
    /// unzip itself refuses absolute paths and strips "../" components.
    public func extract(zip: URL, to destination: URL, fileCount: Int, progress: (Double) -> Void) throws {
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        var done = 0
        progress(0)
        let result = try runner.runStreaming(ContentWriter.unzip, ["-o", zip.path, "-d", destination.path]) { line in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("inflating:") || trimmed.hasPrefix("extracting:") {
                done += 1
                progress(min(Double(done) / Double(max(fileCount, 1)), 1))
            }
        }
        guard result.status == 0 else {
            throw ContentError.extractFailed("failed to extract \(zip.lastPathComponent): \(result.detail)")
        }
        progress(1)
    }

    /// Copies the contents of `source` into `destination`, data only.
    /// Returns relative paths that were skipped (symlinks: FAT can't store them).
    @discardableResult
    public func copyTree(from source: URL, to destination: URL, progress: (Double) -> Void) throws -> [String] {
        let items = try ContentWriter.walk(source)
        let total = max(items.reduce(Int64(0)) { $0 + ($1.kind == .file ? $1.size : 0) }, 1)
        var copied: Int64 = 0
        var skipped: [String] = []
        progress(0)
        for item in items {
            let target = destination.appendingPathComponent(item.relative)
            switch item.kind {
            case .symlink, .other:
                skipped.append(item.relative)
                StampLog.warning("skipping \(item.relative): FAT32 can only store regular files and folders")
            case .directory:
                do {
                    try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
                } catch {
                    throw ContentError.copyFailed("could not create \(item.relative) on the card: \(error.localizedDescription)")
                }
            case .file:
                let sourcePath = source.appendingPathComponent(item.relative).path
                if copyfile(sourcePath, target.path, nil, copyfile_flags_t(COPYFILE_DATA)) != 0 {
                    throw ContentError.copyFailed("copying \(item.relative) failed: \(String(cString: strerror(errno)))")
                }
                copied += item.size
                progress(min(Double(copied) / Double(total), 1))
            }
        }
        progress(1)
        StampLog.info("copied \(items.count) entries from \(source.path) to \(destination.path)")
        return skipped
    }

    /// Best-effort removal of what macOS adds to a FAT volume on its own:
    /// Spotlight/Trash/fsevents folders at the root and AppleDouble "._" files.
    /// Anything that also exists in `source` (the zip's own contents) is kept.
    public func removeMacMetadata(volume: URL, source: URL) {
        let fm = FileManager.default
        for name in ContentWriter.macMetadataNames where !ContentWriter.exists(source.appendingPathComponent(name)) {
            try? fm.removeItem(at: volume.appendingPathComponent(name))
        }
        guard let entries = try? ContentWriter.walk(volume) else { return }
        for entry in entries where entry.kind == .file
            && (entry.relative as NSString).lastPathComponent.hasPrefix("._")
            && !ContentWriter.exists(source.appendingPathComponent(entry.relative)) {
            unlink(volume.appendingPathComponent(entry.relative).path)
        }
    }

    /// Spot-check that the expected top-level entries exist after the copy.
    public func missingTopLevel(at mountPoint: URL, expected: [String]) throws -> [String] {
        let actual: Set<String>
        do {
            actual = Set(try ContentWriter.names(in: mountPoint.path))
        } catch {
            throw ContentError.copyFailed("could not read back \(mountPoint.path) to verify the write: \(error.localizedDescription)")
        }
        return expected.filter { !actual.contains($0) }
    }

    // MARK: POSIX directory walking
    //
    // Foundation's contentsOfDirectory/enumerator silently hide "._" files,
    // which would both skip them in a zip's contents and make the cleanup
    // above a no-op, so directories are read with readdir/lstat instead.

    enum EntryKind { case file, directory, symlink, other }

    struct Entry {
        var relative: String
        var kind: EntryKind
        var size: Int64
    }

    /// Every entry under `root` (depth-first, parents before children), not following symlinks.
    static func walk(_ root: URL) throws -> [Entry] {
        var entries: [Entry] = []
        func visit(_ relative: String) throws {
            let directory = relative.isEmpty ? root.path : root.appendingPathComponent(relative).path
            for name in try names(in: directory).sorted() {
                let childRelative = relative.isEmpty ? name : "\(relative)/\(name)"
                var info = stat()
                guard lstat(root.appendingPathComponent(childRelative).path, &info) == 0 else {
                    throw ContentError.copyFailed("cannot read \(childRelative): \(String(cString: strerror(errno)))")
                }
                let kind: EntryKind
                switch info.st_mode & S_IFMT {
                case S_IFREG: kind = .file
                case S_IFDIR: kind = .directory
                case S_IFLNK: kind = .symlink
                default: kind = .other
                }
                entries.append(Entry(relative: childRelative, kind: kind, size: Int64(info.st_size)))
                if kind == .directory { try visit(childRelative) }
            }
        }
        try visit("")
        return entries
    }

    static func names(in directory: String) throws -> [String] {
        guard let handle = opendir(directory) else {
            throw ContentError.copyFailed("cannot read \(directory): \(String(cString: strerror(errno)))")
        }
        defer { closedir(handle) }
        var names: [String] = []
        while let entry = readdir(handle) {
            let name = withUnsafeBytes(of: entry.pointee.d_name) { raw in
                String(cString: raw.bindMemory(to: CChar.self).baseAddress!)
            }
            if name != "." && name != ".." { names.append(name) }
        }
        return names
    }

    static func exists(_ url: URL) -> Bool {
        var info = stat()
        return lstat(url.path, &info) == 0
    }

    private func firstLine(_ text: String) -> String {
        text.split(separator: "\n").first.map { $0.trimmingCharacters(in: .whitespaces) } ?? text
    }
}
