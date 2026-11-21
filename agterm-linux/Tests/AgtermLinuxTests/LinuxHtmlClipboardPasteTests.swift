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
    }

    @Test(arguments: ["text/uri-list", "x-special/gnome-copied-files", "application/vnd.portal.filetransfer"])
    func mixedFileOfferIsRefused(_ mime: String) throws {
        let builder = try #require(gdk_content_formats_builder_new())
        gdk_content_formats_builder_add_mime_type(builder, "text/plain")
        gdk_content_formats_builder_add_mime_type(builder, mime)
        let formats = try #require(gdk_content_formats_builder_free_to_formats(builder))
        defer { gdk_content_formats_unref(formats) }
        #expect(LinuxHtmlClipboardPaste.offer(formats) == .fileTransfer)
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
    }
}
