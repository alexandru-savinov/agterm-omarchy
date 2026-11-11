// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "GeneratedConfigStorageProposal",
    targets: [
        .target(name: "AgtermLinux"),
        .testTarget(name: "AgtermLinuxTests", dependencies: ["AgtermLinux"]),
    ]
)
