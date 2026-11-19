// Temporary GTK EGL compatibility (2026-11-19, #485).
// Remove with the launcher's preload and its tests once the supported Omarchy channels
// pass the native Wayland gates with upstream GTK or Mesa and the preload disabled.
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <dlfcn.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

// Match GDK's window requests before appending a renderable type. GDK issues the same
// request once for the count and again to fill the array, so both calls need the retry.
static size_t gdk_attribute_count(const EGLint *attrs)
{
    if (!attrs)
        return 0;

    const EGLint keys[] = {
        EGL_SURFACE_TYPE, EGL_COLOR_BUFFER_TYPE, EGL_RED_SIZE,
        EGL_GREEN_SIZE, EGL_BLUE_SIZE, EGL_ALPHA_SIZE,
    };
    EGLint values[] = {EGL_WINDOW_BIT, EGL_RGB_BUFFER, 8, 8, 8, 8};

    for (size_t i = 0; i < sizeof(keys) / sizeof(*keys); ++i) {
        if (attrs[2 * i] != keys[i])
            return 0;
        if (keys[i] == EGL_RED_SIZE && attrs[2 * i + 1] == 9)
            values[2] = values[3] = values[4] = 9;
        if (attrs[2 * i + 1] != values[i])
            return 0;
    }

    if (attrs[12] == EGL_NONE)
        return 12;
    if (values[2] == 9 && attrs[12] == EGL_COLOR_COMPONENT_TYPE_EXT &&
        attrs[13] == EGL_DONT_CARE && attrs[14] == EGL_NONE)
        return 14;
    return 0;
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

    size_t length = gdk_attribute_count(attrs);
    if (length == 0 || !called_from_gtk(__builtin_extract_return_addr(__builtin_return_address(0))))
        return ok;

    const char *vendor = eglQueryString(display, EGL_VENDOR);
    if (!vendor || strcmp(vendor, "Mesa Project") != 0)
        return ok;

    EGLint retry[17];
    memcpy(retry, attrs, length * sizeof(*attrs));
    retry[length] = EGL_RENDERABLE_TYPE;
    retry[length + 1] = EGL_OPENGL_BIT;
    retry[length + 2] = EGL_NONE;
    ok = eglChooseConfig(display, retry, configs, size, count);
    if (ok && !configs && *count > 0)
        fprintf(stderr, "agterm: GTK EGL compatibility retry matched %d configs (desktop OpenGL)\n", *count);
    return ok;
}

// GTK calls libepoxy's exported function pointer, which resolves EGL through a library
// handle. Interpose just that pointer and forward to the public libEGL entry point.
__attribute__((visibility("default")))
PFNEGLCHOOSECONFIGPROC epoxy_eglChooseConfig = choose_config;
