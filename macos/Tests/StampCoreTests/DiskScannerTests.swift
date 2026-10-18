import XCTest
@testable import StampCore

final class DiskScannerTests: XCTestCase {
    override func setUp() { StampLog.isEnabled = false }

    func testSkipsAPFSContainersAndDiskImages() throws {
        let disks = try DiskScanner(runner: Fixtures.scanningRunner()).scan()
        XCTAssertEqual(disks.map(\.identifier), ["disk0", "disk4"])
    }

    func testOnlyQueriesInfoForNonContainerWholeDisks() throws {
        let runner = Fixtures.scanningRunner()
        _ = try DiskScanner(runner: runner).scan()
        let infoTargets = runner.calls.filter { $0.contains("info") }.map { $0.last! }
        XCTAssertEqual(infoTargets, ["disk0", "disk4", "disk7"])
    }

    func testAttributesAPFSVolumesAndRootSnapshotToPhysicalDisk() throws {
        let disks = try DiskScanner(runner: Fixtures.scanningRunner()).scan()
        let ssd = try XCTUnwrap(disks.first { $0.identifier == "disk0" })
        XCTAssertTrue(ssd.mountPoints.contains("/"))
        XCTAssertTrue(ssd.mountPoints.contains("/System/Volumes/Data"))
        XCTAssertTrue(ssd.hostsSystemContainer)
        XCTAssertTrue(ssd.isInternal)
        XCTAssertFalse(ssd.removable)
    }

    func testParsesRemovableCard() throws {
        let disks = try DiskScanner(runner: Fixtures.scanningRunner()).scan()
        let card = try XCTUnwrap(disks.first { $0.identifier == "disk4" })
        XCTAssertTrue(card.removable)
        XCTAssertEqual(card.sizeBytes, 63_864_569_856)
        XCTAssertEqual(card.displayModel, "SD Card Reader")
        XCTAssertEqual(card.busProtocol, "USB")
        XCTAssertEqual(card.mountPoints, ["/Volumes/OLDCARD"])
        XCTAssertEqual(card.partitions.map(\.identifier), ["disk4s1"])
        XCTAssertFalse(card.hostsSystemContainer)
    }

    func testDiskutilFailureIsReported() {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil list -plist", CommandResult(status: 1, stderr: "boom"))
        XCTAssertThrowsError(try DiskScanner(runner: runner).scan()) { error in
            XCTAssertTrue(error.localizedDescription.contains("boom"))
        }
    }

    func testGarbageOutputIsReported() {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil list -plist", CommandResult(status: 0, stdout: Data("not a plist".utf8)))
        XCTAssertThrowsError(try DiskScanner(runner: runner).scan())
    }

    func testDisksSortNumerically() {
        let list: [String: Any] = ["AllDisksAndPartitions": [
            ["DeviceIdentifier": "disk10", "Size": 1],
            ["DeviceIdentifier": "disk2", "Size": 1],
        ]]
        let info: [String: Any] = ["RemovableMedia": true, "VirtualOrPhysical": "Physical"]
        let disks = DiskScanner.buildDisks(list: list, infos: ["disk10": info, "disk2": info])
        XCTAssertEqual(disks.map(\.identifier), ["disk2", "disk10"])
    }

    func testHumanSizeMatchesLinuxFormatting() {
        XCTAssertEqual(humanSize(512), "512 B")
        XCTAssertEqual(humanSize(1536), "1.5 KB")
        XCTAssertEqual(humanSize(64 * 1024 * 1024 * 1024), "64.0 GB")
    }
}
