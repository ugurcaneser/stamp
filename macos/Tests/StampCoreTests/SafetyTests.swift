import XCTest
@testable import StampCore

final class SafetyTests: XCTestCase {
    func testBootDiskIsBlockedWithNoOverride() throws {
        let disks = try DiskScanner(runner: Fixtures.scanningRunner()).scan()
        let ssd = try XCTUnwrap(disks.first { $0.identifier == "disk0" })
        let check = Safety.evaluate(ssd, allowNonRemovable: true)
        XCTAssertFalse(check.ok)
        XCTAssertTrue(check.blockingReasons.contains { $0.contains("running operating system") })
    }

    func testCandidatesExcludeBootDisk() throws {
        let disks = try DiskScanner(runner: Fixtures.scanningRunner()).scan()
        XCTAssertEqual(Safety.candidates(disks).map(\.identifier), ["disk4"])
    }

    func testRemovableCardIsAllowed() {
        let check = Safety.evaluate(Fixtures.card())
        XCTAssertTrue(check.ok)
        XCTAssertFalse(check.requiresOverride)
    }

    func testNonRemovableNeedsOverride() {
        let disk = Fixtures.card(removable: false)
        let refused = Safety.evaluate(disk)
        XCTAssertFalse(refused.ok)
        XCTAssertTrue(refused.requiresOverride)
        let allowed = Safety.evaluate(disk, allowNonRemovable: true)
        XCTAssertTrue(allowed.ok)
        XCTAssertEqual(allowed.warnings.count, 1)
    }

    func testProtectedMountBlocksEvenWithOverride() {
        var disk = Fixtures.card(removable: false)
        disk.mountPoints = ["/Users/someone/stuff"]
        XCTAssertFalse(Safety.evaluate(disk, allowNonRemovable: true).ok)
    }

    func testSystemContainerBlocks() {
        var disk = Fixtures.card()
        disk.hostsSystemContainer = true
        XCTAssertFalse(Safety.evaluate(disk).ok)
    }

    func testReadOnlyBlocks() {
        var disk = Fixtures.card()
        disk.writable = false
        XCTAssertFalse(Safety.evaluate(disk).ok)
    }

    func testProtectedPrefixMatchingIsWholeComponent() {
        XCTAssertTrue(Safety.isProtected(mountPoint: "/"))
        XCTAssertTrue(Safety.isProtected(mountPoint: "/System/Volumes/Data"))
        XCTAssertFalse(Safety.isProtected(mountPoint: "/Volumes/SYSTEM"))
        XCTAssertFalse(Safety.isProtected(mountPoint: "/Usersdata"))
    }
}
