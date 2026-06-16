// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "BricksCore",
    platforms: [.iOS(.v17), .watchOS(.v10)],
    products: [
        .library(name: "BricksCore", targets: ["BricksCore"]),
    ],
    targets: [
        .target(
            name: "BricksCore",
            path: "Sources/BricksCore",
            resources: [
                .process("Splat/GaussianSplat.metal"),
                .process("Resources/Fonts"),
                .process("Resources/Photography"),
            ]
        ),
    ]
)
