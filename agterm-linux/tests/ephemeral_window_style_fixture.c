// Inspect the actual GTK style of the launch window, independently of the X11 compositor.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <gtk/gtk.h>
#include <stdint.h>
#include <stdio.h>

void gtk_window_present(GtkWindow *window) {
    void (*present)(GtkWindow *) = dlsym(RTLD_NEXT, "gtk_window_present");
    present(window);

    const char *trace = g_getenv("AGTERM_EPHEMERAL_STYLE_TRACE");
    if (!trace) return;
    GtkWidget *widget = GTK_WIDGET(window);
    GtkSnapshot *snapshot = gtk_snapshot_new();
    gtk_snapshot_render_background(snapshot, gtk_widget_get_style_context(widget), 0, 0, 16, 16);
    GskRenderNode *node = gtk_snapshot_free_to_node(snapshot);
    cairo_surface_t *image = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 16, 16);
    cairo_t *cr = cairo_create(image);
    if (node) {
        gsk_render_node_draw(node, cr);
        gsk_render_node_unref(node);
    }
    cairo_destroy(cr);
    cairo_surface_flush(image);
    const uint32_t *pixels = (const uint32_t *)cairo_image_surface_get_data(image);
    unsigned alpha = pixels[8 * 16 + 8] >> 24;
    cairo_surface_destroy(image);

    FILE *file = fopen(trace, "a");
    if (!file) return;
    fprintf(file, "%d %u\n", gtk_widget_has_css_class(widget, "agterm-translucent"), alpha);
    fclose(file);
}
