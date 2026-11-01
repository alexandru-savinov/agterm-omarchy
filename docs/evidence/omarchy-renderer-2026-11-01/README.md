# Persistent renderer acceptance on Omarchy ARM64

The existing native-built installation passes the seven-check Wayland harness and an actual installed desktop-entry launch after applying the reviewed software-rendering wrapper.
Both launches inherited `LIBGL_ALWAYS_SOFTWARE=0`; the persistent marker selected software rendering inside the app launcher.
The desktop-launch probe confirmed the running app received `LIBGL_ALWAYS_SOFTWARE=1`.

This evidence covers native payload `5e37cb75ce3d4879ca8b7c74ed28d0548780163a` with the wrapper from [8458e16d](https://github.com/alexandru-savinov/agterm-omarchy/commit/8458e16d06b2432cd6895955b6be972b1edae93d).
It does not accept a hosted archive, Live mode, hardware rendering, or native x86_64 operation.
The separate [hosted ARM64 reconnect failure](https://github.com/alexandru-savinov/agterm-omarchy/issues/52#issuecomment-6560770783) still prevents artifact promotion.

| Record | What it establishes |
| --- | --- |
| `installation.json` | Only the app wrapper changed; the software marker was added and the previous wrapper retained for rollback. |
| `payload-original.json`, `payload-installed.json` | 674 original files became 675; all 673 other original files, including app/CLI/zmx and source/version metadata, remain byte-identical. |
| `ordinary-wayland.json` | Seven native checks pass, independent cleanup tracks 23 processes without leftovers, and pre-existing apps are preserved. |
| `desktop-launch.json` | `gio launch` uses the real installed desktop entry with isolated state; native Wayland mapping, GL-surface realization, PTY execution/read-back, effective renderer mode and owned-process cleanup pass. |
| `metadata.json` | Native payload identity, wrapper source, actual harness digest, shell, renderer mode and qualification boundaries. |
| `capture-hashes.json` | Hashes of unchanged operator-side captures; images are withheld because an unrelated desktop weather popup overlaps the test window. |

The ordinary harness is byte-identical to [the script at 8458e16d](https://github.com/alexandru-savinov/agterm-omarchy/blob/8458e16d06b2432cd6895955b6be972b1edae93d/scripts/test-omarchy.sh), SHA-256 `c051a4d2c74f7df3f6a2d9c93eabe0001ba481c254e4454cd5380b8f8b9c3c19`.
It checks terminal input/read-back, red-to-green rendered pixels, workspace/session/split operations, program overlays, WebKit HTML-overlay load state, teardown, and absence of GL-context/CSS failures.
The HTML check establishes reported load state; it is not an HTML pixel-rendering assertion.

An initial run stopped at the focus assertion because the open Omarchy launcher menu held exclusive layer-shell keyboard focus.
Dismissing only that transient menu through its own `shell.hide(omarchy.menu)` API allowed the unchanged focus and pixel assertions to pass.
The compositor, other panels, existing application process and user configuration were preserved.
No test assertion was weakened, no user shell was changed, and no running user terminal was restarted.

Verify the public records from this directory:

```sh
sha256sum -c SHA256SUMS
```

The [operator report](https://github.com/alexandru-savinov/agterm-omarchy/issues/56#issuecomment-6560840863) records the acceptance and separate remaining release gate.
Private host identifiers, absolute home paths, control transcripts and unrelated desktop content are omitted from this public bundle.
