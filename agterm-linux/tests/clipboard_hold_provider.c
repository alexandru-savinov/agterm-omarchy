/* Test-only clipboard owner: hold the first real transfer until RELEASE arrives on stdin. */
#include <gtk/gtk.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    GdkContentProvider parent;
    const char *text;
    GTask *pending;
    GOutputStream *stream;
    gboolean holding;
    guint requested;
    guint completed;
} HeldProvider;
typedef GdkContentProviderClass HeldProviderClass;
G_DEFINE_TYPE(HeldProvider, held_provider, GDK_TYPE_CONTENT_PROVIDER)

static GdkContentFormats *formats(GdkContentProvider *provider) {
    (void)provider;
    const char *mime = "text/plain;charset=utf-8";
    return gdk_content_formats_new(&mime, 1);
}

static void written(GObject *stream, GAsyncResult *result, gpointer data) {
    GTask *task = data;
    HeldProvider *self = g_task_get_source_object(task);
    GError *error = NULL;
    gsize count = 0;
    if (g_output_stream_write_all_finish(G_OUTPUT_STREAM(stream), result, &count, &error)) {
        g_print("DONE %u\n", ++self->completed);
        g_task_return_boolean(task, TRUE);
    } else {
        g_printerr("clipboard transfer failed: %s\n", error->message);
        g_task_return_error(task, error);
    }
    g_object_unref(task);
}

static void transfer(HeldProvider *self, GOutputStream *stream, GTask *task) {
    g_output_stream_write_all_async(stream, self->text, strlen(self->text),
                                   G_PRIORITY_DEFAULT, g_task_get_cancellable(task), written, task);
}

static void write_async(GdkContentProvider *provider, const char *mime, GOutputStream *stream,
                        int priority, GCancellable *cancellable,
                        GAsyncReadyCallback callback, gpointer data) {
    HeldProvider *self = (HeldProvider *)provider;
    GTask *task = g_task_new(provider, cancellable, callback, data);
    g_task_set_priority(task, priority);
    if (strcmp(mime, "text/plain;charset=utf-8") != 0 || self->pending) {
        g_task_return_new_error(task, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED, "unexpected clipboard transfer");
        g_object_unref(task);
        return;
    }
    g_print("REQUESTED %u\n", ++self->requested);
    if (self->holding) {
        self->pending = task;
        self->stream = g_object_ref(stream);
    } else {
        transfer(self, stream, task);
    }
}

static gboolean write_finish(GdkContentProvider *provider, GAsyncResult *result, GError **error) {
    g_return_val_if_fail(g_task_is_valid(result, provider), FALSE);
    return g_task_propagate_boolean(G_TASK(result), error);
}

static void held_provider_class_init(HeldProviderClass *klass) {
    klass->ref_formats = formats;
    klass->write_mime_type_async = write_async;
    klass->write_mime_type_finish = write_finish;
}

static void held_provider_init(HeldProvider *self) { self->holding = TRUE; }

static GMainLoop *loop;
static gboolean input(GIOChannel *channel, GIOCondition condition, gpointer data) {
    HeldProvider *self = data;
    if (condition & (G_IO_HUP | G_IO_ERR)) {
        g_main_loop_quit(loop);
        return G_SOURCE_REMOVE;
    }
    char *line = NULL;
    if (g_io_channel_read_line(channel, &line, NULL, NULL, NULL) != G_IO_STATUS_NORMAL)
        return G_SOURCE_CONTINUE;
    if (g_strcmp0(line, "RELEASE\n") == 0 && self->pending) {
        GTask *task = self->pending;
        self->pending = NULL;
        self->holding = FALSE;
        g_print("RELEASED\n");
        transfer(self, self->stream, task);
        g_clear_object(&self->stream);
    } else {
        g_printerr("unexpected fixture command\n");
        g_main_loop_quit(loop);
    }
    g_free(line);
    return G_SOURCE_CONTINUE;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    setvbuf(stdout, NULL, _IONBF, 0);
    gtk_init();
    HeldProvider *provider = g_object_new(held_provider_get_type(), NULL);
    provider->text = argv[1];
    GdkClipboard *clipboard = gdk_display_get_clipboard(gdk_display_get_default());
    if (!gdk_clipboard_set_content(clipboard, GDK_CONTENT_PROVIDER(provider))) return 3;
    loop = g_main_loop_new(NULL, FALSE);
    GIOChannel *channel = g_io_channel_unix_new(0);
    g_io_add_watch(channel, G_IO_IN | G_IO_HUP | G_IO_ERR, input, provider);
    g_print("READY\n");
    g_main_loop_run(loop);
    if (provider->pending) {
        g_task_return_new_error(provider->pending, G_IO_ERROR, G_IO_ERROR_CANCELLED, "fixture stopped");
        g_clear_object(&provider->pending);
    }
    g_clear_object(&provider->stream);
    gdk_clipboard_set_content(clipboard, NULL);
    g_object_unref(provider);
    g_io_channel_unref(channel);
    g_main_loop_unref(loop);
    return 0;
}
