#!/usr/bin/env bash
# Make a DIAGNOSTIC COPY of a staged payload whose bin/zmx is a wrapper running the
# unchanged zmx bytes under qemu-aarch64 user mode with one CPU model. The source payload is
# never modified; the copy's zmx.real is compared byte-for-byte with the original.
# Usage: make-qemu-copy.sh <stage-dir> <dest-dir> <qemu-cpu-model> <qemu-aarch64-path>
set -euo pipefail
stage="$(readlink -f "${1:?stage}")"
dest="${2:?dest}"
model="${3:?cpu model}"
qemu="$(readlink -f "${4:?qemu path}")"
[[ "$model" =~ ^[a-z0-9,=-]+$ ]] || { echo "invalid cpu model: $model" >&2; exit 2; }
[[ -x "$qemu" ]] || { echo "qemu not executable: $qemu" >&2; exit 2; }
[[ ! -e "$dest" ]] || { echo "destination exists: $dest" >&2; exit 2; }
cp -a "$stage" "$dest"
mv "$dest/bin/zmx" "$dest/bin/zmx.real"
cat > "$dest/bin/zmx" <<EOF
#!/bin/sh
# Diagnostic wrapper (ultra_arm64_runtime #64): unchanged zmx bytes under qemu-aarch64 -cpu $model.
exec $qemu -cpu $model "\$(dirname "\$(readlink -f "\$0")")/zmx.real" "\$@"
EOF
chmod 755 "$dest/bin/zmx"
cmp -s "$dest/bin/zmx.real" "$stage/bin/zmx"
echo "diagnostic copy $dest: zmx.real identical to payload zmx; wrapper cpu=$model"
