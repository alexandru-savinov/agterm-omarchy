import CGtk
import Foundation

// Window-wide messages over the content: the persistent no-GL-context notice and transient toasts.
@MainActor
extension AppController {
    /// Show a persistent, centered message when the GtkGLArea can't create a GL context (VM/headless/
    /// llvmpipe-less/Wayland-no-GL) — the terminal can't render, so explain it instead of a blank pane.
    func showGLError() {
        guard let overlay = deckOverlay, glErrorLabel == nil else { return }
        let presentation = LinuxSurfaceFailurePresentation.resolve(.glContext, role: .main)
        guard presentation.scope == .displayWide,
              let label = op(gtk_label_new(presentation.message)) else { return }
        gtk_label_set_justify(label, GTK_JUSTIFY_CENTER)
        gtk_label_set_wrap(label, 1)
        gtk_widget_set_halign(W(label), GTK_ALIGN_CENTER)
        gtk_widget_set_valign(W(label), GTK_ALIGN_CENTER)
        gtk_widget_add_css_class(W(label), "agterm-gl-error")
        glErrorLabel = label
        gtk_overlay_add_overlay(overlay, W(label))
    }

    /// Surface a transient banner (AdwToast) over the window content — keymap/config parse diagnostics
    /// and other non-modal alerts. No-op before the content (and its toast overlay) is built.
    func showToast(_ message: String) {
        guard let overlay = toastOverlay else { return }
        message.withCString { adw_toast_overlay_add_toast(overlay, adw_toast_new($0)) }
    }
}
