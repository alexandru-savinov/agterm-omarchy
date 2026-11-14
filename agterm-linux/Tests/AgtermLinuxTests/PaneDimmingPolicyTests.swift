import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux pane dimming policy under translucent windows")
struct PaneDimmingPolicyTests {
    @Test("the mute scales by the rendered window opacity and clamps both inputs", arguments: [
        (0.5, 0.8, 0.4), (0.5, 1.0, 0.5), (0.5, 0.0, 0.0), (1.5, 0.5, 0.5), (-0.2, 0.8, 0.0), (0.5, 2.0, 0.5),
    ])
    func scaledMute(mute: Double, window: Double, expected: Double) {
        #expect(abs(AppController.scaledMuteOpacity(mute, renderedWindowOpacity: window) - expected) < 1e-9)
    }

    @Test("only the inactive split pane is washed, and a backdrop clears both")
    func washFollowsFocus() {
        func wash(_ pane: OverlayPane, splitFocused: Bool, isSplit: Bool = true, backdrop: Bool = false) -> Double {
            AppController.paneOverlayWashOpacity(
                isSplit: isSplit, splitFocused: splitFocused, pane: pane, scaledMuteOpacity: 0.4, backdropActive: backdrop)
        }
        #expect(wash(.left, splitFocused: false) == 0)
        #expect(wash(.right, splitFocused: false) == 0.4)
        #expect(wash(.left, splitFocused: true) == 0.4)
        #expect(wash(.right, splitFocused: true) == 0)
        for pane in OverlayPane.allCases {
            #expect(wash(pane, splitFocused: true, isSplit: false) == 0)
            #expect(wash(pane, splitFocused: true, backdrop: true) == 0)
        }
    }

    @Test("surface opacities dim the unfocused split pane only")
    func surfaceOpacities() {
        let dimmed = 0.6
        let leftFocused = AppController.paneSurfaceOpacities(isSplit: true, splitFocused: false, dimmed: dimmed, backdropActive: false)
        #expect(leftFocused.left == 1 && leftFocused.right == dimmed)
        let rightFocused = AppController.paneSurfaceOpacities(isSplit: true, splitFocused: true, dimmed: dimmed, backdropActive: false)
        #expect(rightFocused.left == dimmed && rightFocused.right == 1)
        let single = AppController.paneSurfaceOpacities(isSplit: false, splitFocused: true, dimmed: dimmed, backdropActive: false)
        #expect(single.left == 1 && single.right == 1)
        let backdrop = AppController.paneSurfaceOpacities(isSplit: true, splitFocused: true, dimmed: dimmed, backdropActive: true)
        #expect(backdrop.left == 1 && backdrop.right == 1)
    }

    @Test("floating frames dim only behind the quick terminal")
    func floatingFrames() {
        #expect(AppController.floatingFrameOpacity(quickVisible: true, dimmed: 0.6) == 0.6)
        #expect(AppController.floatingFrameOpacity(quickVisible: false, dimmed: 0.6) == 1)
    }

    @Test("the wash colour prefers the overlay's fixed background, then the theme, then black")
    func washColor() {
        #expect(AppController.paneOverlayWashColor(fixedBackground: "#112233", themeBackground: "#445566") == "#112233")
        #expect(AppController.paneOverlayWashColor(fixedBackground: nil, themeBackground: "#445566") == "#445566")
        #expect(AppController.paneOverlayWashColor(fixedBackground: nil, themeBackground: nil) == "#000000")
    }
}
