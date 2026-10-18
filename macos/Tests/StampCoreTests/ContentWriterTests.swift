import XCTest
@testable import StampCore

/// Runs the real /usr/bin/unzip and real file copies against temp directories.
final class ContentWriterTests: XCTestCase {
    private var dir: URL!

    override func setUp() {
        StampLog.isEnabled = false
        dir = makeTempDir()
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
    }

    func testPreviewCountsFilesSizeAndTopLevel() throws {
        let zip = makeZip(["a.txt": "hi", "dir with space/b c.txt": "there"], in: dir)
        let preview = try ContentWriter().preview(zip: zip)
        XCTAssertEqual(preview.fileCount, 2)
        XCTAssertEqual(preview.uncompressedBytes, 7)
        XCTAssertEqual(preview.topLevelNames, ["a.txt", "dir with space"])
    }

    func testCorruptZipIsRejected() throws {
        let zip = dir.appendingPathComponent("bad.zip")
        try Data("definitely not a zip".utf8).write(to: zip)
        XCTAssertThrowsError(try ContentWriter().preview(zip: zip))
    }

    func testMissingZipIsRejected() {
        XCTAssertThrowsError(try ContentWriter().validate(zip: dir.appendingPathComponent("nope.zip")))
    }

    func testCapacityWarning() {
        let preview = ZipPreview(fileCount: 1, uncompressedBytes: 2000, topLevelNames: ["a"])
        XCTAssertEqual(preview.warnings(targetCapacity: 1000).count, 1)
        XCTAssertTrue(preview.warnings(targetCapacity: 4000).isEmpty)
        XCTAssertTrue(preview.warnings(targetCapacity: nil).isEmpty)
    }

    func testParseListingHandlesSpacesAndDirectories() {
        let output = """
        Archive:  t.zip
          Length      Date    Time    Name
        ---------  ---------- -----   ----
                3  10-18-2026 21:28   a.txt
                0  10-18-2026 21:28   dir with space/
                6  10-18-2026 21:28   dir with space/b c.txt
        ---------                     -------
                9                     3 files
        """
        let rows = ContentWriter.parseListing(output)
        XCTAssertEqual(rows.map(\.name), ["a.txt", "dir with space/", "dir with space/b c.txt"])
        XCTAssertEqual(rows.map(\.size), [3, 0, 6])
    }

    func testExtractAndCopyRoundTrip() throws {
        let zip = makeZip(["a.txt": "hi", "nested/deeper/c.txt": "deep"], in: dir)
        let extracted = dir.appendingPathComponent("extracted")
        var extractProgress: [Double] = []
        try ContentWriter().extract(zip: zip, to: extracted, fileCount: 2) { extractProgress.append($0) }
        XCTAssertEqual(extractProgress.last, 1)

        let card = dir.appendingPathComponent("card")
        try FileManager.default.createDirectory(at: card, withIntermediateDirectories: true)
        var copyProgress: [Double] = []
        let skipped = try ContentWriter().copyTree(from: extracted, to: card) { copyProgress.append($0) }
        XCTAssertTrue(skipped.isEmpty)
        XCTAssertEqual(copyProgress.last, 1)
        XCTAssertEqual(try String(contentsOf: card.appendingPathComponent("nested/deeper/c.txt"), encoding: .utf8), "deep")
        XCTAssertEqual(try ContentWriter().missingTopLevel(at: card, expected: ["a.txt", "nested"]), [])
        XCTAssertEqual(try ContentWriter().missingTopLevel(at: card, expected: ["a.txt", "gone"]), ["gone"])
    }

    func testCopySkipsSymlinks() throws {
        let src = dir.appendingPathComponent("src")
        try FileManager.default.createDirectory(at: src, withIntermediateDirectories: true)
        try "x".write(to: src.appendingPathComponent("real.txt"), atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(atPath: src.appendingPathComponent("link").path, withDestinationPath: "real.txt")
        let card = dir.appendingPathComponent("card")
        try FileManager.default.createDirectory(at: card, withIntermediateDirectories: true)
        let skipped = try ContentWriter().copyTree(from: src, to: card) { _ in }
        XCTAssertEqual(skipped, ["link"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: card.appendingPathComponent("real.txt").path))
    }

    func testCopyDoesNotCarryExtendedAttributes() throws {
        let src = dir.appendingPathComponent("src")
        try FileManager.default.createDirectory(at: src, withIntermediateDirectories: true)
        let file = src.appendingPathComponent("f.txt")
        try "x".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertEqual(setxattr(file.path, "com.example.test", "v", 1, 0, 0), 0)
        let card = dir.appendingPathComponent("card")
        try FileManager.default.createDirectory(at: card, withIntermediateDirectories: true)
        try ContentWriter().copyTree(from: src, to: card) { _ in }
        // macOS stamps com.apple.provenance on new files itself; only our attribute matters.
        XCTAssertEqual(getxattr(card.appendingPathComponent("f.txt").path, "com.example.test", nil, 0, 0, 0), -1)
    }

    /// Foundation's file writing treats "._name" as AppleDouble data and never
    /// creates the file, so these fixtures go through POSIX directly.
    private func writeRaw(_ url: URL) {
        let fd = open(url.path, O_CREAT | O_WRONLY | O_TRUNC, 0o644)
        XCTAssertGreaterThanOrEqual(fd, 0)
        _ = write(fd, "x", 1)
        close(fd)
    }

    func testRemoveMacMetadataKeepsZipContents() throws {
        let src = dir.appendingPathComponent("src")
        let card = dir.appendingPathComponent("card")
        for url in [src, card, card.appendingPathComponent(".Spotlight-V100"), card.appendingPathComponent(".fseventsd")] {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        }
        for url in [card.appendingPathComponent("._a.txt"), card.appendingPathComponent("a.txt"),
                    src.appendingPathComponent("._keep"), card.appendingPathComponent("._keep")] {
            writeRaw(url)
        }
        ContentWriter().removeMacMetadata(volume: card, source: src)
        let left = Set(try ContentWriter.names(in: card.path))
        XCTAssertEqual(left, ["a.txt", "._keep"])
    }

    func testCopyIncludesAppleDoubleFilesFromTheZip() throws {
        let src = dir.appendingPathComponent("src")
        try FileManager.default.createDirectory(at: src.appendingPathComponent("__MACOSX"), withIntermediateDirectories: true)
        writeRaw(src.appendingPathComponent("__MACOSX/._firmware.bin"))
        let card = dir.appendingPathComponent("card")
        try FileManager.default.createDirectory(at: card, withIntermediateDirectories: true)
        try ContentWriter().copyTree(from: src, to: card) { _ in }
        XCTAssertTrue(ContentWriter.exists(card.appendingPathComponent("__MACOSX/._firmware.bin")))
    }
}
