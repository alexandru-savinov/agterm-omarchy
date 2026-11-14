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
export IMAGE=ghcr.io/alexandru-savinov/agterm-omarchy-build-x86_64@sha256:ec9531b315a437be617134cf796d5ab78bd234bc6b5ba8c8a0685254caa3a1dc
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

## Optional cached fish supplement

The image has no fish. A separately verified Nix fish 4.0.8 can exercise the quoting regression without
changing the image or primary launcher. This is supplemental Nix fish, **not Ubuntu apt fish or a pure-image
test**. The five paths below are the observed ELF runtime, including the libgcc symlink target; they are
enough for the tested `fish --no-config` command, not a claim about all interactive fish features.
Require these paths to exist already; this procedure does not download or build them.
The supplement also requires Nix and bubblewrap/kernel support for temporary overlays (tested with
bubblewrap 0.11.2), beyond the base user/mount/PID namespace requirement.

```bash
export FISH_STORE=/nix/store/qq6iknnizywfjjclczrhxx0y6jvll84m-fish-4.0.8
python3 - <<'PY'
import os, pathlib, shlex, subprocess
root = pathlib.Path(os.environ['VALIDATION_ROOT'])
fish = os.environ['FISH_STORE']
paths = [fish,
    '/nix/store/n6vl6ni6zxxgh4163cqnjf8p0n7yah0d-pcre2-10.44',
    '/nix/store/cf1a53iqg6ncnygl698c4v0l8qam5a2q-gcc-14.3.0-lib',
    '/nix/store/d84l3qjb077dxkajn24k58z2q261d4lv-gcc-14.3.0-libgcc',
    '/nix/store/i3ibgfskl99qd8rslafbpaa1dmxdzh1z-glibc-2.40-66']
subprocess.run(['nix', '--extra-experimental-features', 'nix-command', '--offline',
                'store', 'verify', '--sigs-needed', '1', *paths], check=True)
source = (root / 'run').read_text()
mounts = ' '.join('--ro-bind ' + shlex.quote(path) + ' ' + shlex.quote(path) for path in paths)
edits = {
    '--ro-bind "$root/bundle/rootfs" /':
        '--overlay-src "$root/bundle/rootfs" --tmp-overlay / --dir /nix/store ' + mounts,
    '--proc /proc --dev /dev --clearenv': '--proc /proc --dev /dev --remount-ro / --clearenv',
    '--setenv PATH /opt/': '--setenv PATH ' + fish + '/bin:/opt/',
    '"$root/home"': '"${AGTERM_FISH_HOME:-$root/fish-profile/home}"',
    '"$root/workspace"': '"${AGTERM_FISH_WORKSPACE:-$root/fish-profile/workspace}"',
    '"$root/tmp"': '"${AGTERM_FISH_TMP:-$root/fish-profile/tmp}"',
}
for before, after in edits.items():
    assert source.count(before) == 1, before
    source = source.replace(before, after)
for directory in ['home', 'workspace', 'tmp/runtime-ubuntu']:
    (root / 'fish-profile' / directory).mkdir(parents=True, exist_ok=True)
(root / 'fish-profile/home').chmod(0o700)
(root / 'fish-profile/tmp/runtime-ubuntu').chmod(0o700)
target = root / 'run-fish'
target.write_text(source)
target.chmod(0o755)
PY
"$VALIDATION_ROOT/run-fish" /bin/bash -c 'fish --version; fish --no-config -c "printf fish-ok"; command -v sh swift'
```

The namespace-only overlay creates missing `/nix/store` mountpoints, then becomes read-only before
execution. Original image bytes remain untouched. Only fish's bin directory is added to PATH; no
Nix loader variables or other Nix command directories replace Ubuntu tools. Fish's verified package NAR
hash is `sha256-6VycLRLsjCN25nCwZvvDyBS3ILaw+Hantd52uh1dYKY=`. Keep the offline signature/content-verification
result with the test evidence rather than treating a path name as signature verification.

After the current build owner releases the shared checkout, select the exact reviewed commit and reuse
the existing 6.3.2 workspace/home/tmp at unchanged container paths:

```bash
AGTERM_FISH_HOME="$VALIDATION_ROOT/home" \
AGTERM_FISH_WORKSPACE="$VALIDATION_ROOT/workspace" \
AGTERM_FISH_TMP="$VALIDATION_ROOT/tmp" \
  "$VALIDATION_ROOT/run-fish" /bin/bash -c \
  'cd /workspace/agtermCore && swift test --jobs 2 --filter CommandRestoreTests'
```

[Executed PR #278 evidence](https://github.com/alexandru-savinov/agterm-omarchy/pull/278#issuecomment-6800308946)
at `80be556a8541d9fe9f4fb5e893479aff7b5b7066` passed all 38 tests with Swift 6.3.2 and this supplemental
fish 4.0.8, including the real fish roundtrip (not skipped). This does not replace hosted architecture
checks or native runtime acceptance.
