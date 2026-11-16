import Foundation
import XCTest
@testable import agterm

final class AppKitSavedStateTests: XCTestCase {
    func testLaunchRegistersIgnoringAppKitSavedState() {
        // the registration domain specifically: Xcode can pass the same key as a launch argument
        let registered = UserDefaults.standard.volatileDomain(forName: UserDefaults.registrationDomain)
        XCTAssertEqual(registered[AppKitSavedState.ignoreKey] as? Bool, true)
    }
}
