# Native GTK graphics comparison — 13 November 2026

The default graphics route in the tested Omarchy ARM VM cannot create the
desktop OpenGL context used by the selected agterm frontend. A process-scoped
software renderer can create it. This is a bounded diagnosis of the current VM,
not a claim about every Omarchy machine or a release acceptance result.

## Observed results

| Actual check | Result | Scope |
| --- | --- | --- |
| Existing EGL/GLX diagnostic utilities, default route | Successful queries; virtual renderer reports compatibility GL 2.1 and no core profile | Driver capability observation; not an agterm test |
| Minimal GtkGLArea, desktop GL only, default route | Context creation failed | Separate test helper |
| Identical helper with `LIBGL_ALWAYS_SOFTWARE=1` | Context created, desktop GL 4.6 | Separate test helper; explicit software override |
| Selected agterm archive, default ordinary lane | Failed 0/7; no EGL configuration available | [Exact-product attempt](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6839289587) |
| Selected agterm archive, software ordinary lane | Passed 7/7 | [Earlier exact-product partial attempt](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6838341466) |
| Selected agterm archive, software Live lane | Passed 9/9 with the disclosed login-shell fixture | Same partial attempt; not the owner's real login-shell route |

The earlier product attempt failed its first config probe and did not run later
config attempts. Neither the successful helper nor the successful ordinary/Live
subsets make that overall attempt pass. A later config-only execution stopped
before any frontend/probe launch on a helper-permission defect, tracked in
[#386](https://github.com/alexandru-savinov/agterm-omarchy/issues/386).

## Method

Root used the existing native Omarchy ARM VM and its actual desktop route.
Existing `eglinfo`, `glxinfo` and `lspci` utilities were queried as the normal
test account with fresh private HOME/cache/config directories. No driver,
package, service, graphics configuration or installed payload was changed.

The [test-only C source](gtk-context-probe.c) creates and realizes a GtkWindow and
GtkGLArea, chooses `GDK_GL_API_GL`, and asks for the current context. It never
presents the window and never invokes agterm. The selected product makes the
same [desktop-GL-only API choice](https://github.com/alexandru-savinov/agterm-omarchy/blob/4c267ac4d01c2ac843fbc9775fd9d87e30c7e34a/agterm-linux/Sources/AgtermLinux/GhosttySurface.swift#L138).

The helper was compiled using existing native GTK headers/libraries and the
existing C compiler. Both executions used fresh private file roots and the
memory GSettings backend. The default execution added no renderer override;
the second changed only `LIBGL_ALWAYS_SOFTWARE=1`. Source, helper-binary and
compiler hashes were checked before/after. The source published here is exactly
the source used by that comparison:

```text
SHA-256 95b6ecb4972b40474e188f4600325546f7df4646b491b5bd6f1b3ae83adbe01c
```

The helper's default exit was 2 with `Unable to create a GL context`; software
exit was 0 with API GL and version 4.6. The utility queries separately reported
the default virtual-renderer capability. Together these observations support a
VM/GTK graphics compatibility explanation; they do not prove that every failure
inside the full product has the same cause.

GTK's [context version contract](https://docs.gtk.org/gdk4/method.GLContext.set_required_version.html)
says GTK enforces its minimum supported version even when a caller requests
less. This comparison does not lower the product's API/version requirements or
make GLES support a claimed workaround.

## Exact product boundary

The selected product source is
`4c267ac4d01c2ac843fbc9775fd9d87e30c7e34a`, from
[full CI 43301307779](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/43301307779).
The separate helper is not part of that product artifact and did not modify it.
Results are not transferred to later main/upstream builds. The existing scoped
[#56 software-renderer remedy](https://github.com/alexandru-savinov/agterm-omarchy/issues/56) retains its
original acceptance boundary.

Default-renderer product acceptance, the config matrix, broader native UI,
Mac GUI Live, and final integrated release acceptance remain incomplete.
[#305](https://github.com/alexandru-savinov/agterm-omarchy/issues/305) tracks the current queue.

This note contains selected public-source identities and sanitized observations.
Raw host/VM diagnostics and private execution records remain with the operator.
