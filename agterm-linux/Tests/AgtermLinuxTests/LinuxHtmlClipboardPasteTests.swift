import CGtk
import Testing
@testable import AgtermLinux

@MainActor
@Suite("HTML clipboard offer classification")
struct LinuxHtmlClipboardPasteTests {
    @Test func emptyAndUnavailableOffersAreUnresolved() throws {
        let formats = try #require(gdk_content_formats_new(nil, 0))
        defer { gdk_content_formats_unref(formats) }
        #expect(LinuxHtmlClipboardPaste.offer(nil) == .unresolved)
        #expect(LinuxHtmlClipboardPaste.offer(formats) == .unresolved)
        #expect(LinuxHtmlClipboardPaste.refusesPrimary(formats))
    }

    @Test(arguments: ["text/uri-list", "x-special/gnome-copied-files", "application/vnd.portal.filetransfer"])
    func mixedFileOfferIsRefused(_ mime: String) throws {
        let builder = try #require(gdk_content_formats_builder_new())
        gdk_content_formats_builder_add_mime_type(builder, "text/plain")
        gdk_content_formats_builder_add_mime_type(builder, mime)
        let formats = try #require(gdk_content_formats_builder_free_to_formats(builder))
        defer { gdk_content_formats_unref(formats) }
        #expect(LinuxHtmlClipboardPaste.offer(formats) == .fileTransfer)
        #expect(LinuxHtmlClipboardPaste.refusesPrimary(formats))
    }

    @Test func gdkFileTypeIsRefused() throws {
        let formats = try #require(gdk_content_formats_new_for_gtype(gdk_file_list_get_type()))
        defer { gdk_content_formats_unref(formats) }
        #expect(LinuxHtmlClipboardPaste.offer(formats) == .fileTransfer)
    }

    @Test func plainTextIsClassifiedAndAllowed() throws {
        let builder = try #require(gdk_content_formats_builder_new())
        gdk_content_formats_builder_add_mime_type(builder, "text/plain")
        let formats = try #require(gdk_content_formats_builder_free_to_formats(builder))
        defer { gdk_content_formats_unref(formats) }
        #expect(LinuxHtmlClipboardPaste.offer(formats) == .other)
        #expect(!LinuxHtmlClipboardPaste.refusesPrimary(formats))
    }

    @Test func gtkPasteShortcutsUseTheirOwnEditingCommand() {
        let command = LinuxHtmlClipboardPaste.command
        let ctrl = GDK_CONTROL_MASK.rawValue
        let shift = GDK_SHIFT_MASK.rawValue
        let alt = GDK_ALT_MASK.rawValue
        #expect(command(UInt32(GDK_KEY_v), GDK_CONTROL_MASK) == .paste)
        #expect(command(UInt32(GDK_KEY_V), GdkModifierType(rawValue: ctrl | shift)) == .plainText)
        #expect(command(UInt32(GDK_KEY_Insert), GDK_SHIFT_MASK) == .paste)
        #expect(command(UInt32(GDK_KEY_KP_Insert), GDK_SHIFT_MASK) == .paste)
        #expect(command(UInt32(GDK_KEY_KP_Insert), GDK_CONTROL_MASK) == nil)
        #expect(command(UInt32(GDK_KEY_v), GdkModifierType(rawValue: ctrl | alt)) == nil)
        #expect(command(UInt32(GDK_KEY_V), GdkModifierType(rawValue: ctrl | shift | alt)) == nil)
    }

    @Test func resolvedTextPastesOnceOutsideTheChangeEmission() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        fixture.change(.other)
        fixture.change(.other)
        #expect(fixture.pastes.isEmpty)
        fixture.fire(0)
        fixture.change(.other)
        fixture.fire(0)
        #expect(fixture.pastes == [1])
        #expect(fixture.changed == nil && fixture.timers.isEmpty)
    }

    @Test func resolvedFilesNeverPaste() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        fixture.change(.fileTransfer)
        fixture.change(.other)
        fixture.fire(0)
        #expect(fixture.pastes.isEmpty)
        #expect(fixture.changed == nil && fixture.timers.isEmpty)
    }

    @Test func aSecondEmptyNotificationKeepsTheOriginalDeadline() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        fixture.change(.unresolved)
        #expect(fixture.pastes.isEmpty && fixture.timers.keys.sorted() == [2])
        fixture.change(.other)
        fixture.fire(0)
        #expect(fixture.pastes == [1])
    }

    @Test func expiryRefusesALateChange() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        let late = fixture.changed
        fixture.fire(2)
        fixture.offer = .other
        late?()
        fixture.fire(0)
        #expect(fixture.pastes.isEmpty)
        #expect(fixture.changed == nil && fixture.timers.isEmpty)
    }

    @Test func aSecondPasteKeyReplacesTheFirst() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        let stale = fixture.changed
        #expect(fixture.request(2))
        fixture.change(.other)
        stale?()
        fixture.fire(0)
        #expect(fixture.pastes == [2])
    }

    @Test func modifiersPreserveTheRequestButOtherKeysCancelIt() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        fixture.paste.keyPressed(isModifier: true)
        fixture.change(.other)
        fixture.fire(0)
        fixture.change(.unresolved)
        #expect(fixture.request(2))
        fixture.paste.keyPressed(isModifier: false)
        fixture.change(.other)
        fixture.fire(0)
        #expect(fixture.pastes == [1])
    }

    @Test(arguments: ["closed", "focus-left", "window-inactive"])
    func cancellationDiscardsAnAlreadyQueuedEditingCommand(_ reason: String) {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        let late = fixture.changed
        fixture.change(.other)
        let queuedResume = fixture.timers[0]
        fixture.paste.cancel(reason)
        late?()
        queuedResume?()
        #expect(fixture.pastes.isEmpty)
        #expect(fixture.changed == nil && fixture.timers.isEmpty)
    }

    @Test func aChangeBeforeReplayCanStillRefuseTheOffer() {
        let fixture = PasteFixture()
        #expect(fixture.request(1))
        fixture.change(.other)
        fixture.offer = .fileTransfer
        fixture.fire(0)
        #expect(fixture.pastes.isEmpty && fixture.timers.isEmpty)
    }

    @Test func nonX11EmptyOfferIsConsumedWithoutWaiting() {
        let fixture = PasteFixture()
        #expect(fixture.request(1, hold: false))
        #expect(fixture.changed == nil && fixture.timers.isEmpty)
        fixture.offer = .other
        #expect(!fixture.request(2, hold: false))
        #expect(fixture.pastes.isEmpty)
    }
}

@MainActor
private final class PasteFixture {
    typealias Action = LinuxHtmlClipboardPaste.Action
    var offer = LinuxHtmlClipboardPaste.Offer.unresolved
    var changed: Action?
    var timers: [Double: Action] = [:]
    var pastes: [Int] = []

    lazy var paste = LinuxHtmlClipboardPaste { [unowned self] delay, fire in
        timers[delay] = fire
        return { [unowned self] in timers[delay] = nil }
    }

    func request(_ id: Int, hold: Bool = true) -> Bool {
        let source = LinuxHtmlClipboardPaste.Source(offer: { [unowned self] in offer },
                                                    observe: { [unowned self] handler in
            changed = handler
            return { [unowned self] in changed = nil }
        })
        return paste.intercept(source, holdUnresolved: hold) { [unowned self] in pastes.append(id) }
    }

    func change(_ new: LinuxHtmlClipboardPaste.Offer) {
        offer = new
        changed?()
    }

    func fire(_ delay: Double) {
        timers.removeValue(forKey: delay)?()
    }
}
