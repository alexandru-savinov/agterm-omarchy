// Temporary GTK EGL compatibility (2026-11-19, #485).
// Remove with the launcher's preload and its tests once the supported Omarchy channels
// pass the native Wayland gates with upstream GTK or Mesa and the preload disabled.
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

// GTK uses this RGBA8 request for both the count and the config array.
static const EGLint desktop_request[] = {
    EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
    EGL_COLOR_BUFFER_TYPE, EGL_RGB_BUFFER,
    EGL_RED_SIZE, 8,
    EGL_GREEN_SIZE, 8,
    EGL_BLUE_SIZE, 8,
    EGL_ALPHA_SIZE, 8,
    EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
    EGL_NONE,
};

static bool is_gdk_window_request(const EGLint *attrs)
{
    if (!attrs)
        return false;
    size_t count = sizeof(desktop_request) / sizeof(*desktop_request) - 3;
    for (size_t i = 0; i < count; ++i) {
        if (attrs[i] != desktop_request[i])
            return false;
    }
    return attrs[count] == EGL_NONE;
}

static bool called_from_gtk(void *caller)
{
    Dl_info info;
    if (!dladdr(caller, &info) || !info.dli_fname)
        return false;
    const char *filename = strrchr(info.dli_fname, '/');
    filename = filename ? filename + 1 : info.dli_fname;
    static const char gtk_soname[] = "libgtk-4.so";
    size_t prefix = sizeof(gtk_soname) - 1;
    return strncmp(filename, gtk_soname, prefix) == 0 &&
           (filename[prefix] == '\0' || filename[prefix] == '.');
}

static EGLBoolean choose_config(EGLDisplay display, const EGLint *attrs,
                               EGLConfig *configs, EGLint size, EGLint *count)
{
    EGLBoolean ok = eglChooseConfig(display, attrs, configs, size, count);
    if (!ok || !count || *count != 0 || (configs && size <= 0))
        return ok;

    if (!is_gdk_window_request(attrs) ||
        !called_from_gtk(__builtin_extract_return_addr(__builtin_return_address(0))))
        return ok;

    const char *vendor = eglQueryString(display, EGL_VENDOR);
    if (!vendor || strcmp(vendor, "Mesa Project") != 0)
        return ok;

    ok = eglChooseConfig(display, desktop_request, configs, size, count);
    if (ok && !configs && *count > 0)
        fprintf(stderr, "agterm: GTK EGL compatibility retry matched %d configs (desktop OpenGL)\n", *count);
    return ok;
}

// GTK calls libepoxy's exported function pointer, which resolves EGL through a library
// handle. Interpose just that pointer and forward to the public libEGL entry point.
__attribute__((visibility("default")))
PFNEGLCHOOSECONFIGPROC epoxy_eglChooseConfig = choose_config;
