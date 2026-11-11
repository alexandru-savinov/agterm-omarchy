# Reusable local Linux validation

Use one serially owned Linux-filesystem checkout and cache to avoid repeated cold builds and host
`/bin` or `/usr/bin` mismatches. This is local x86_64 evidence, not hosted CI or native Omarchy acceptance.
Do not reuse Swift 6.4 build products with this Swift 6.3.2 environment.

The existing build image is based on the official Swift 6.3.2 Ubuntu 24.04 image and already includes
GTK, Zig and the main test tools. Its [publication and original attestation recovery](../packaging/linux/github-feature-evidence.md#package-original-attestation-and-oidc)
record source `67f744ba7e60525e92a7a0a635b26eb4aab965b6`. Pull the immutable digest below, not a mutable tag.
The procedure verifies content identity; it does not perform a fresh signature verification.

Requirements: Linux user/mount/PID namespaces, Bash, Git, Python 3.11+, skopeo, umoci and bubblewrap.
The initial setup used skopeo 1.24.1, umoci 0.6.0 and bubblewrap 0.11.2 without a container daemon.
Use an ephemeral tool environment rather than changing host services or global policy.
Allow about 10 GiB before builds: the measured download is 1,707,132,576 bytes; retained original and
converted OCI stores used 1.6 GiB each and the unpacked bundle 5.2 GiB. Build caches need additional space.

## Pull and unpack once

Choose a new disposable directory on a Linux filesystem. Keep the exported variables for these commands.
The narrowly scoped policy accepts this approved image by digest, not by a newly verified signature;
all other image sources are rejected. TLS verification stays enabled.

```bash
export VALIDATION_ROOT=$(mktemp -d /tmp/agterm-ubuntu.XXXXXX)
export IMAGE=ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64@sha256:e2d48733b2fd8481c314bd639fd15e523110e106418cb982cc830a7e620a839f
python3 - <<'PY'
import json, os, pathlib
root = pathlib.Path(os.environ['VALIDATION_ROOT'])
allow = [{'type': 'insecureAcceptAnything'}]
policy = {'default': [{'type': 'reject'}], 'transports': {
    'docker': {os.environ['IMAGE']: allow}, 'oci': {str(root / 'oci'): allow}}}
(root / 'policy.json').write_text(json.dumps(policy))
PY
skopeo --policy "$VALIDATION_ROOT/policy.json" copy --preserve-digests \
  "docker://$IMAGE" "oci:$VALIDATION_ROOT/oci:build"
```

Verify the preserved manifest and every content blob before unpacking:

```bash
python3 - <<'PY'
import hashlib, json, os, pathlib
root = pathlib.Path(os.environ['VALIDATION_ROOT']) / 'oci'
manifest = json.loads((root / 'index.json').read_text())['manifests'][0]
assert manifest['digest'] == os.environ['IMAGE'].split('@')[1]
def path(descriptor):
    algorithm, digest = descriptor['digest'].split(':')
    assert algorithm == 'sha256'
    return root / 'blobs' / algorithm / digest
document = json.loads(path(manifest).read_text())
for descriptor in [manifest, document['config'], *document['layers']]:
    blob = path(descriptor)
    assert blob.stat().st_size == descriptor['size']
    with blob.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == descriptor['digest'].split(':')[1]
print('Pinned manifest/config/layer hashes verified')
PY
# umoci requires OCI media types; the published manifest uses Docker v2 media types.
# Retain the original pinned content separately. This conversion needs no second download.
skopeo --policy "$VALIDATION_ROOT/policy.json" copy --format oci \
  "oci:$VALIDATION_ROOT/oci:build" "oci:$VALIDATION_ROOT/oci-unpack:build"
umoci unpack --rootless --image "$VALIDATION_ROOT/oci-unpack:build" "$VALIDATION_ROOT/bundle"
```

## Self-contained checkout and isolated runtime

An ordinary linked worktree points outside the mounted directory. Clone instead, without hardlinks or
alternates, and check out the exact reviewed SHA. Replace the two example variables with the selected
local repository and commit; never overwrite another worker's active checkout.

```bash
LOCAL_REPOSITORY=/path/to/local/repository
REVIEWED_SHA=replace-with-full-reviewed-commit
git clone --no-hardlinks --no-checkout "$LOCAL_REPOSITORY" "$VALIDATION_ROOT/workspace"
git -C "$VALIDATION_ROOT/workspace" checkout --detach "$REVIEWED_SHA"
test ! -e "$VALIDATION_ROOT/workspace/.git/objects/info/alternates"
mkdir -p "$VALIDATION_ROOT/home" "$VALIDATION_ROOT/tmp/runtime-ubuntu"
chmod 700 "$VALIDATION_ROOT/home" "$VALIDATION_ROOT/tmp/runtime-ubuntu"
cat > "$VALIDATION_ROOT/run" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bwrap --die-with-parent --new-session --unshare-user --uid 1000 --gid 1000 \
  --unshare-pid --cap-drop ALL --ro-bind "$root/bundle/rootfs" / \
  --bind "$root/workspace" /workspace --bind "$root/home" /home/ubuntu \
  --bind "$root/tmp" /tmp --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --proc /proc --dev /dev --clearenv \
  --setenv PATH /opt/zig-x86_64-linux-0.16.0:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  --setenv HOME /home/ubuntu --setenv USER ubuntu --setenv LOGNAME ubuntu \
  --setenv SHELL /bin/bash --setenv LANG C.UTF-8 \
  --setenv XDG_CONFIG_HOME /home/ubuntu/.config --setenv XDG_CACHE_HOME /home/ubuntu/.cache \
  --setenv XDG_DATA_HOME /home/ubuntu/.local/share --setenv XDG_RUNTIME_DIR /tmp/runtime-ubuntu \
  --setenv LINUX_SOURCEKIT_LIB_PATH /usr/lib/libsourcekitdInProc.so \
  --chdir /workspace "$@"
SH
chmod 755 "$VALIDATION_ROOT/run"
```

Only the checkout, temporary directory and isolated home are writable. No host home, credentials,
Nix library paths or compiler caches are mounted. The pinned image contains the non-root `ubuntu`
account at UID/GID 1000, with `/home/ubuntu` and `/bin/bash`; these match the launcher environment.

## Probe before compiling

```bash
"$VALIDATION_ROOT/run" /bin/bash -c '
set -eu
test "$(id -u)" = 1000
getent passwd "$(id -u)"
test ! -w /usr/bin
test -w "$HOME"
git rev-parse HEAD
swift --version
zig version
swiftlint version
pkg-config --modversion gtk4 libadwaita-1 webkitgtk-6.0 epoxy
for tool in sh bash dirname git rg zsh file python3 Xvfb dbus-run-session shellcheck; do command -v "$tool"; done
if command -v fish; then fish --version; else echo "fish absent: fish checks not validated"; fi
python3 -c "import os,pwd,socket; p=pwd.getpwuid(os.getuid()); assert p.pw_name==os.environ[\"USER\"]; assert p.pw_dir==os.environ[\"HOME\"]; assert p.pw_shell==os.environ[\"SHELL\"]; a,b=socket.socketpair(); a.close(); b.close()"
'
```

The initial probes observed Swift 6.3.2, Zig 0.16.0, SwiftLint 0.65.0, GTK 4.14.5, libadwaita 1.5.0,
WebKitGTK 2.52.6 and epoxy 1.5.10, with standard `/usr/bin` tools and working socket pairs. Fish was
absent. Record any separately supplied fish version; do not count a skipped test as coverage.
The first local CLI build used a writable disposable root and unmapped UID 1001. The corrected launcher
above passed read-only-root, writable-home, passwd/HOME/SHELL consistency and socket probes; do not
retroactively describe that earlier build as using the corrected isolation or account identity.

Coordinate one compiler owner before building. For the actual Linux CLI, run
`"$VALIDATION_ROOT/run" /bin/bash -c 'cd /workspace/agterm-linux && swift build --product agtermctl-linux --jobs 2'`.
Keep that checkout/cache for subsequent reviewed commits, verifying a clean tree and exact SHA each time.
Keep its HOME path stable too: Clang PCM files embed the module-cache path. The initial move from
`/home/builder` to `/home/ubuntu` required moving the old isolated `~/.cache/clang/ModuleCache` aside
and regenerating it. Preserve SwiftPM dependencies and the checkout rather than deleting every cache.
Do not switch its commit while another process is compiling or testing. Full GTK/vendor builds are a
separate cost and are not implied by a CLI build. Keep local results separate from hosted and native evidence.
