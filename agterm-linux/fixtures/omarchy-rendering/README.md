# Omarchy terminal color references

These two unchanged PNGs preserve the isolated terminal red/green rendering reference from native release run `wayland-release.edzrnW`.
They are a bounded Git LFS transfer fixture, not a portable pixel-perfect golden test or a new runtime result.
Compositor scaling, font configuration, and driver output can differ across desktops.
The canonical [native acceptance bundle](https://github.com/alexandru-savinov/agterm-omarchy/tree/634f667684a16bebb4c14b7f1ccce87d7edabde9/docs/evidence/omarchy-arm64-2026-10-29)
retains the full result, provenance limitations, and cleanup correction history.

[fixtures.json](fixtures.json) records each SHA256/LFS object ID, exact size, source run, evidence commit, and original binary metadata.
Together the images occupy 603,409 bytes, below the task's 5 MiB limit.
Only PNGs in this directory use LFS; no historical or unrelated assets were migrated.

With Git LFS available, fetch this directory's objects from the checkout:

```sh
git lfs pull --include='agterm-linux/fixtures/omarchy-rendering/*.png' --exclude=''
```

A checkout that skipped LFS may contain pointer text instead of images.
Verify the materialized files from this directory using Python's standard library:

```sh
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('fixtures.json').read_text())
for item in manifest['files']:
    data = Path(item['path']).read_bytes()
    assert data.startswith(b'\x89PNG\r\n\x1a\n'), item['path']
    assert len(data) == item['bytes'], item['path']
    assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
    print(item['path'], 'verified')
PY
```

The [LFS task](https://github.com/alexandru-savinov/agterm-omarchy/issues/28) records capability/usage checks and the actual fresh-transfer result.
GitHub charges LFS storage and download usage to the applicable account allowance; these files do not enable paid overages or change billing settings.
