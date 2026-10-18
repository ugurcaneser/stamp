// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "Stamp",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Stamp", targets: ["Stamp"]),
    ],
    targets: [
        // All disk/zip/safety logic. No SwiftUI/AppKit imports, so it stays
        // unit-testable the same way core/ is on the Linux side.
        .target(name: "StampCore"),
        .executableTarget(name: "Stamp", dependencies: ["StampCore"]),
        .testTarget(name: "StampCoreTests", dependencies: ["StampCore"]),
    ]
)
