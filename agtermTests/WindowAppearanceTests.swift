import AppKit
import XCTest
@testable import agterm

@MainActor
final class WindowAppearanceTests: XCTestCase {
    private var window: NSWindow!

    override func setUp() async throws {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
    }

    override func tearDown() async throws {
        window.orderOut(nil)
        window = nil
    }

    func testTranslucentWindowKeepsSavedOpacityWithoutReduceTransparency() throws {
        WindowAppearance.sync(window: window, background: .black,
                              chrome: .init(opacity: 0.6, blurRadius: 10, reduceTransparency: false))

        XCTAssertFalse(window.isOpaque)
        XCTAssertEqual(try XCTUnwrap(window.backgroundColor).alphaComponent, 0.6, accuracy: 0.001)
    }

    func testReduceTransparencyPresentsTranslucentWindowOpaque() throws {
        WindowAppearance.sync(window: window, background: .black,
                              chrome: .init(opacity: 0.6, blurRadius: 10, reduceTransparency: true))

        XCTAssertTrue(window.isOpaque)
        XCTAssertEqual(try XCTUnwrap(window.backgroundColor).alphaComponent, 1, accuracy: 0.001)
    }
}
