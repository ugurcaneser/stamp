import XCTest
@testable import StampCore

final class FormatterTests: XCTestCase {
    override func setUp() { StampLog.isEnabled = false }

    private func formatter(_ runner: FakeRunner) -> DiskFormatter {
        DiskFormatter(runner: runner, sleep: { _ in })
    }

    func testSanitizeLabelMatchesLinuxRules() {
        XCTAssertEqual(DiskFormatter.sanitizeLabel(nil), "STAMP")
        XCTAssertEqual(DiskFormatter.sanitizeLabel("   "), "STAMP")
        XCTAssertEqual(DiskFormatter.sanitizeLabel("my card"), "MY CARD")
        XCTAssertEqual(DiskFormatter.sanitizeLabel("averyveryverylonglabel"), "AVERYVERYVE")
        XCTAssertEqual(DiskFormatter.sanitizeLabel("bad/:*chars"), "BADCHARS")
        XCTAssertEqual(DiskFormatter.sanitizeLabel("ŞİĞ"), "STAMP")
    }

    func testWholeDiskIdentifierValidation() {
        XCTAssertTrue(DiskFormatter.isWholeDiskIdentifier("disk4"))
        XCTAssertFalse(DiskFormatter.isWholeDiskIdentifier("disk4s1"))
        XCTAssertFalse(DiskFormatter.isWholeDiskIdentifier("/dev/disk4"))
        XCTAssertFalse(DiskFormatter.isWholeDiskIdentifier("disk4; rm -rf /"))
    }

    func testEraseForcesFAT32WithMBR() throws {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil eraseDisk FAT32 MY CARD MBRFormat /dev/disk4", CommandResult(status: 0))
        try formatter(runner).erase(Fixtures.card(), label: "my card")
        XCTAssertEqual(runner.calls, [["/usr/sbin/diskutil", "eraseDisk", "FAT32", "MY CARD", "MBRFormat", "/dev/disk4"]])
    }

    func testEraseRejectsPartitionIdentifier() {
        var disk = Fixtures.card()
        disk.identifier = "disk4s1"
        let runner = FakeRunner()
        XCTAssertThrowsError(try formatter(runner).erase(disk, label: "X"))
        XCTAssertTrue(runner.calls.isEmpty)
    }

    func testEraseFailureCarriesDiskutilMessage() {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil eraseDisk FAT32 STAMP MBRFormat /dev/disk4", CommandResult(status: 1, stderr: "Error: -69877: Couldn't open device"))
        XCTAssertThrowsError(try formatter(runner).erase(Fixtures.card(), label: "")) { error in
            XCTAssertTrue(error.localizedDescription.contains("Couldn't open device"))
        }
    }

    func testMountPointReturnsExistingMount() throws {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil info -plist disk4s1", plistResult([
            "FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "MountPoint": "/Volumes/STAMP",
        ]))
        XCTAssertEqual(try formatter(runner).mountPoint(ofPartition: "disk4s1"), "/Volumes/STAMP")
    }

    func testMountPointMountsWhenNeeded() throws {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil info -plist disk4s1",
                  plistResult(["FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "MountPoint": ""]),
                  plistResult(["FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "MountPoint": "/Volumes/STAMP"]))
        runner.on("/usr/sbin/diskutil mount disk4s1", CommandResult(status: 0))
        XCTAssertEqual(try formatter(runner).mountPoint(ofPartition: "disk4s1"), "/Volumes/STAMP")
        XCTAssertTrue(runner.calls.contains(["/usr/sbin/diskutil", "mount", "disk4s1"]))
    }

    func testMountPointWaitsForPartitionToAppear() throws {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil info -plist disk4s1",
                  CommandResult(status: 1, stderr: "Could not find disk"),
                  plistResult(["FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32", "MountPoint": "/Volumes/STAMP"]))
        XCTAssertEqual(try formatter(runner).mountPoint(ofPartition: "disk4s1"), "/Volumes/STAMP")
    }

    func testRejectsNonFAT32Result() {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil info -plist disk4s1", plistResult([
            "FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT16", "MountPoint": "/Volumes/STAMP",
        ]))
        XCTAssertThrowsError(try formatter(runner).mountPoint(ofPartition: "disk4s1"))
    }

    func testEjectUsesDeviceNode() throws {
        let runner = FakeRunner()
        runner.on("/usr/sbin/diskutil eject /dev/disk4", CommandResult(status: 0))
        try formatter(runner).eject(Fixtures.card())
    }
}
