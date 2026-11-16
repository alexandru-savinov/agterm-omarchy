#include <webkit/webkit.h>
#include <gio/gio.h>
#include <errno.h>
#include <stdint.h>
#include <sys/syscall.h>
#include <unistd.h>
#if __has_include(<gio/gunixinputstream.h>)
#include <gio/gunixinputstream.h>
#else
// webkitgtk-6.0's cflags need not name gio-unix-2.0's include directory; libgio-2.0 exports the symbol.
GInputStream *g_unix_input_stream_new(gint fd, gboolean close_fd);
#endif

// Keep the variadic GObject construction at the C boundary: Swift cannot import g_object_new.
static inline GtkWidget *agterm_web_view_new_ephemeral(WebKitUserContentManager *content_manager,
                                                       WebKitSettings *settings)
{
    WebKitNetworkSession *session = webkit_network_session_new_ephemeral();
    GtkWidget *view = g_object_new(WEBKIT_TYPE_WEB_VIEW,
                                   "network-session", session,
                                   "user-content-manager", content_manager,
                                   "settings", settings,
                                   NULL);
    g_object_unref(session);
    return view;
}

static inline void agterm_uri_scheme_finish_stream(WebKitURISchemeRequest *request,
                                                    GInputStream *stream, const char *content_type)
{
    WebKitURISchemeResponse *response = webkit_uri_scheme_response_new(stream, -1);
    webkit_uri_scheme_response_set_content_type(response, content_type);
    // Granted files and remote snapshots can change at the same URI between overlay reloads.
    // WebKit otherwise keeps their scripts and styles in its memory cache after re-reading the page.
    SoupMessageHeaders *headers = soup_message_headers_new(SOUP_MESSAGE_HEADERS_RESPONSE);
    soup_message_headers_append(headers, "Cache-Control", "no-store");
    webkit_uri_scheme_response_set_http_headers(response, headers); // Takes ownership of headers.
    webkit_uri_scheme_request_finish_with_response(request, response);
    g_object_unref(response);
}

static inline void agterm_uri_scheme_finish_file(WebKitURISchemeRequest *request,
                                                  const char *path, const char *content_type)
{
    GFile *file = g_file_new_for_path(path);
    GError *error = NULL;
    GFileInputStream *stream = g_file_read(file, NULL, &error);
    g_object_unref(file);
    if (!stream) {
        webkit_uri_scheme_request_finish_error(request, error);
        g_error_free(error);
        return;
    }
    agterm_uri_scheme_finish_stream(request, G_INPUT_STREAM(stream), content_type);
    g_object_unref(stream);
}

// Serves an already-open regular file and takes ownership of `fd`: the stream closes it.
static inline void agterm_uri_scheme_finish_descriptor(WebKitURISchemeRequest *request,
                                                        int fd, const char *content_type)
{
    GInputStream *stream = g_unix_input_stream_new(fd, TRUE);
    agterm_uri_scheme_finish_stream(request, stream, content_type);
    g_object_unref(stream);
}

// openat2(2) resolving `path` beneath `dirfd` and refusing every symlink, or -1 with errno ENOSYS without it.
// The struct and RESOLVE_NO_SYMLINKS (0x04) | RESOLVE_BENEATH (0x08) are kernel ABI, spelled here because
// glibc versions differ on whether <fcntl.h> declares `struct open_how`, which <linux/openat2.h> would redefine.
static inline int agterm_openat2_beneath(int dirfd, const char *path, int flags)
{
#ifdef SYS_openat2
    struct { uint64_t flags, mode, resolve; } how = { (uint64_t)flags, 0, 0x04 | 0x08 };
    return (int)syscall(SYS_openat2, dirfd, path, &how, sizeof how);
#else
    errno = ENOSYS;
    return -1;
#endif
}

static inline void agterm_uri_scheme_deny(WebKitURISchemeRequest *request)
{
    GError *error = g_error_new_literal(G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED,
                                       "page resource is outside the granted directory");
    webkit_uri_scheme_request_finish_error(request, error);
    g_error_free(error);
}

static inline void agterm_disconnect_signals(gpointer object, gpointer data)
{
    g_signal_handlers_disconnect_by_data(object, data);
}

static inline GtkWidget *agterm_web_view_new_with_session(WebKitUserContentManager *manager,
                                                         WebKitSettings *settings,
                                                         WebKitNetworkSession *session)
{
    return g_object_new(WEBKIT_TYPE_WEB_VIEW, "network-session", session,
                        "user-content-manager", manager, "settings", settings, NULL);
}
