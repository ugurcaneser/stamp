import XCTest
@testable import StampCore

/// End-to-end flow with faked diskutil and a temp directory standing in for the card.
final class StampJobTests: XCTestCase {
    private var dir: URL!
    private var card: URL!

    override func setUp() {
        StampLog.isEnabled = false
        dir = makeTempDir()
        card = dir.appendingPathComponent("card")
        try! FileManager.default.createDirectory(at: card, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
    }

    private func runner(erase: CommandResult = CommandResult(status: 0)) -> FakeRunner {
        let runner = Fixtures.scanningRunner()
        runner.on("/usr/sbin/diskutil eraseDisk FAT32 BOOT MBRFormat /dev/disk4", erase)
        runner.on("/usr/sbin/diskutil info -plist disk4s1", plistResult([
            "FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "MountPoint": card.path,
        ]))
        runner.on("/usr/sbin/diskutil eject /dev/disk4", CommandResult(status: 0))
        return runner
    }

    /// Fake diskutil, real unzip.
    private func job(_ fake: FakeRunner) -> StampJob {
        StampJob(
            scanner: DiskScanner(runner: fake),
            formatter: DiskFormatter(runner: fake, sleep: { _ in }),
            writer: ContentWriter(),
            flush: {}
        )
    }

    private func request(_ zip: URL, disk: Disk = Fixtures.card(), override: Bool = false) -> JobRequest {
        JobRequest(disk: disk, zip: zip, label: "boot", allowNonRemovable: override)
    }

    func testHappyPathFormatsCopiesVerifiesAndEjects() throws {
        let zip = makeZip(["firmware.bin": "data", "config/settings.txt": "x=1"], in: dir)
        let fake = runner()
        var phases: [Phase] = []
        let result = try job(fake).run(request(zip)) { phase, _, _ in
            if phases.last != phase { phases.append(phase) }
        }
        XCTAssertEqual(phases, Phase.allCases)
        XCTAssertTrue(result.ejected)
        XCTAssertEqual(result.label, "BOOT")
        XCTAssertEqual(try String(contentsOf: card.appendingPathComponent("config/settings.txt"), encoding: .utf8), "x=1")
        let commands = fake.calls.map { $0.dropFirst().joined(separator: " ") }
        let erase = try XCTUnwrap(commands.firstIndex(of: "eraseDisk FAT32 BOOT MBRFormat /dev/disk4"))
        let eject = try XCTUnwrap(commands.firstIndex(of: "eject /dev/disk4"))
        XCTAssertLessThan(erase, eject)
    }

    func testBadZipNeverTouchesTheDisk() throws {
        let zip = dir.appendingPathComponent("bad.zip")
        try Data("nope".utf8).write(to: zip)
        let fake = runner()
        XCTAssertThrowsError(try job(fake).run(request(zip)) { _, _, _ in })
        XCTAssertFalse(fake.calls.contains { $0.contains("eraseDisk") })
    }

    func testSwappedDiskIsRefused() throws {
        let zip = makeZip(["a": "b"], in: dir)
        var stale = Fixtures.card()
        stale.sizeBytes = 32_000_000_000
        let fake = runner()
        XCTAssertThrowsError(try job(fake).run(request(zip, disk: stale)) { _, _, _ in }) { error in
            XCTAssertTrue(error is JobError)
        }
        XCTAssertFalse(fake.calls.contains { $0.contains("eraseDisk") })
    }

    func testBootDiskIsRefusedEvenWithOverride() throws {
        let zip = makeZip(["a": "b"], in: dir)
        let boot = try XCTUnwrap(try DiskScanner(runner: Fixtures.scanningRunner()).scan().first { $0.identifier == "disk0" })
        let fake = runner()
        XCTAssertThrowsError(try job(fake).run(request(zip, disk: boot, override: true)) { _, _, _ in })
        XCTAssertFalse(fake.calls.contains { $0.contains("eraseDisk") })
    }

    func testEraseFailureStopsBeforeCopy() throws {
        let zip = makeZip(["a": "b"], in: dir)
        let fake = runner(erase: CommandResult(status: 1, stderr: "Resource busy"))
        XCTAssertThrowsError(try job(fake).run(request(zip)) { _, _, _ in })
        XCTAssertFalse(FileManager.default.fileExists(atPath: card.appendingPathComponent("a").path))
    }

    func testEjectFailureIsOnlyAWarning() throws {
        let zip = makeZip(["a": "b"], in: dir)
        let fake = runner()
        fake.responses["/usr/sbin/diskutil eject /dev/disk4"] = [CommandResult(status: 1, stderr: "busy")]
        let result = try job(fake).run(request(zip)) { _, _, _ in }
        XCTAssertFalse(result.ejected)
    }
}
