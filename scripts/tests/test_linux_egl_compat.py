"""Exercise launcher loading and the EGL retry with real ELF interposition."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

DRIVER = r"""
#include <EGL/egl.h>
#include <string.h>

int calls;
const char *vendor = "Mesa Project";
EGLBoolean valid = EGL_TRUE;
int default_count;

const char *eglQueryString(EGLDisplay display, EGLint name)
{
    return display == EGL_NO_DISPLAY || name != EGL_VENDOR ? NULL : vendor;
}

EGLBoolean eglChooseConfig(EGLDisplay display, const EGLint *attrs,
                           EGLConfig *configs, EGLint size, EGLint *count)
{
    ++calls;
    if (!valid || display == EGL_NO_DISPLAY)
        return EGL_FALSE;
    if (!count)
        return EGL_FALSE;
    *count = default_count;
    for (; attrs && *attrs != EGL_NONE; attrs += 2) {
        if (attrs[0] == EGL_RENDERABLE_TYPE && attrs[1] == EGL_OPENGL_BIT) {
            *count = 1;
            if (configs && size > 0)
                configs[0] = (EGLConfig)1;
        }
    }
    return EGL_TRUE;
}
"""

EPOXY = r"""
#include <EGL/egl.h>

PFNEGLCHOOSECONFIGPROC epoxy_eglChooseConfig = eglChooseConfig;
"""

GTK = r"""
#include <EGL/egl.h>

extern PFNEGLCHOOSECONFIGPROC epoxy_eglChooseConfig;

EGLBoolean gtk_choose(const EGLint *attrs, EGLConfig *configs, EGLint size, EGLint *count)
{
    return epoxy_eglChooseConfig((EGLDisplay)1, attrs, configs, size, count);
}
"""

PROBE = r"""
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <assert.h>
#include <stdlib.h>
#include <string.h>

extern int calls;
extern int default_count;
extern const char *vendor;
extern EGLBoolean valid;
extern PFNEGLCHOOSECONFIGPROC epoxy_eglChooseConfig;
extern EGLBoolean gtk_choose(const EGLint *, EGLConfig *, EGLint, EGLint *);

#define BASE EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_COLOR_BUFFER_TYPE, EGL_RGB_BUFFER
#define RGB(n) EGL_RED_SIZE, n, EGL_GREEN_SIZE, n, EGL_BLUE_SIZE, n, EGL_ALPHA_SIZE, 8

int main(void)
{
    int active = !(getenv("AGTERM_DISABLE_EGL_COMPAT") &&
                   !strcmp(getenv("AGTERM_DISABLE_EGL_COMPAT"), "1"));
    const EGLint normal[] = {BASE, RGB(8), EGL_NONE};
    const EGLint hdr[] = {BASE, RGB(9), EGL_COLOR_COMPONENT_TYPE_EXT, EGL_DONT_CARE, EGL_NONE};
    const EGLint explicit[] = {BASE, RGB(8), EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_NONE};
    const EGLint unrelated[] = {EGL_NONE};
    EGLConfig cfg = NULL;
    EGLint count = -1;

    assert(gtk_choose(normal, NULL, -1, &count) && count == active);
    assert(calls == 1 + active);
    assert(gtk_choose(normal, &cfg, 1, &count) && count == active);
    assert(cfg == (EGLConfig)(long)active);
    assert(gtk_choose(hdr, NULL, -1, &count) && count == active);

    calls = 0;
    assert(gtk_choose(explicit, NULL, -1, &count) && count == 0);
    assert(gtk_choose(unrelated, NULL, -1, &count) && count == 0);
    assert(gtk_choose(NULL, NULL, -1, &count) && count == 0);
    assert(gtk_choose(normal, &cfg, 0, &count) && count == 0);
    assert(epoxy_eglChooseConfig((EGLDisplay)1, normal, NULL, -1, &count) && count == 0);
    vendor = "Other EGL vendor";
    assert(gtk_choose(normal, NULL, -1, &count) && count == 0);
    vendor = "Mesa Project";
    default_count = 3;
    assert(gtk_choose(normal, NULL, -1, &count) && count == 3);
    valid = EGL_FALSE;
    assert(!gtk_choose(normal, NULL, -1, &count));
    assert(calls == 8);
}
"""


@unittest.skipUnless(platform.system() == 'Linux' and shutil.which('cc'), 'requires a Linux C toolchain')
class EGLCompatTests(unittest.TestCase):
    def test_launcher_retries_only_matching_failed_mesa_calls(self):
        with tempfile.TemporaryDirectory(prefix='agterm EGL ') as folder:
            stage = Path(folder)
            lib = stage / 'lib'
            bin_dir = stage / 'bin'
            lib.mkdir()
            bin_dir.mkdir()
            launcher = bin_dir / 'agterm-linux'
            launcher.write_text((ROOT / 'scripts/stage-linux.sh').read_text().split("<<'LAUNCH'\n")[1]
                                .split('\nLAUNCH\n')[0] + '\n')
            launcher.chmod(0o755)
            for name, contents in [('driver', DRIVER), ('epoxy', EPOXY), ('gtk', GTK), ('probe', PROBE)]:
                (stage / f'{name}.c').write_text(contents)

            def compile_c(output, source, *args):
                subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fPIC',
                                '-fno-optimize-sibling-calls', '-o', str(output), str(source),
                                f'-L{lib}', f'-Wl,-rpath-link,{lib}', *args], check=True)

            compile_c(lib / 'libEGL.so', stage / 'driver.c', '-shared')
            compile_c(lib / 'libepoxy.so', stage / 'epoxy.c', '-shared', '-lEGL')
            compile_c(lib / 'libgtk-4.so', stage / 'gtk.c', '-shared', '-lepoxy')
            compile_c(lib / 'libagterm-egl-compat.so', ROOT / 'linux/egl-compat.c',
                      '-shared', '-fvisibility=hidden', '-Wl,-z,defs', '-lEGL', '-ldl')
            compile_c(bin_dir / 'agterm-linux.bin', stage / 'probe.c',
                      '-lgtk-4', '-lEGL', '-lepoxy')
            env = dict(os.environ, AGTERM_DISABLE_EGL_COMPAT='0')
            env.pop('LD_PRELOAD', None)
            result = subprocess.run([launcher], env=env, check=True, capture_output=True, text=True)
            self.assertEqual(result.stderr.count('compatibility retry matched 1 configs'), 2)
            result = subprocess.run([launcher], env=dict(env, AGTERM_DISABLE_EGL_COMPAT='1'),
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
