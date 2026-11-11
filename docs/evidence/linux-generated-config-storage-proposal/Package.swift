// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "GeneratedConfigStorageProposal",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "AgtermLinux"),
        .testTarget(name: "AgtermLinuxTests", dependencies: ["AgtermLinux"]),
    ]
)
