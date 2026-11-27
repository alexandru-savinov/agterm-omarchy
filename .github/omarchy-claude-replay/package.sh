#!/usr/bin/env bash
set -euo pipefail

archive=${1:?pass the current CI archive}
expected_commit=${2:?pass the CI commit}
version=${3:?pass the package version}
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
suite="$repo/.github/omarchy-claude-replay"
recipe="$repo/packaging/omarchy-pkgs/agterm-omarchy-bin"
work="$(mktemp -d /tmp/agterm-claude.XXXXXX)"
chmod 0755 "$work"

commit="$(bsdtar -xOf "$archive" agterm-stage/share/agterm/COMMIT)"
[[ "$commit" == "$expected_commit" ]]
pacman -Syu --noconfirm --needed dbus mesa xorg-server-xvfb xorg-xauth curl python

user=agterm-claude-replay
home="/home/$user"
useradd --create-home --user-group --shell /bin/bash "$user"
install -d -o "$user" -g "$user" "$work/build" "$work/replay"
cp -a "$home" "$work/home-before"
cp -- "$recipe/PKGBUILD" "$recipe/agterm-omarchy-bin.install" "$work/build/"
cp -- "$archive" "$work/build/agterm-runtime.tar.gz"
chown -R "$user:$user" "$work/build"

as_user() {
    runuser --user "$user" -- env -i HOME="$home" USER="$user" LOGNAME="$user" SHELL=/bin/bash PATH=/usr/bin:/bin "$@"
}

digest="$(sha256sum "$archive" | cut -d ' ' -f 1)"
(
    cd "$work/build"
    as_user AGTERM_PACKAGE_VERSION="$version" AGTERM_ARCHIVE_SHA256="$digest" makepkg --nodeps --noconfirm
)
package="$work/build/agterm-omarchy-bin-$version-1-x86_64.pkg.tar.zst"
pacman -U --noconfirm "$package" 2>&1 | tee "$work/install.log"
pacman -U --noconfirm "$package" 2>&1 | tee "$work/upgrade.log"
activation='/usr/bin/agtermctl integration install hooks --only claude'
for log in "$work/install.log" "$work/upgrade.log"; do
    [[ "$(grep -Fc "$activation" "$log")" == 1 ]]
done
pacman -Qql agterm-omarchy-bin > "$work/package-files"
grep -Fxq '/usr/bin/agterm-agent-status' "$work/package-files"
if grep -Eq '^/home(/|$)' "$work/package-files"; then
    echo 'Package payload contains files under /home' >&2
    exit 1
fi
diff -r --no-dereference "$work/home-before" "$home"
echo 'OK: pacman printed activation on install and upgrade, and the fixture HOME is untouched'

# Fetch only the bridge module. Execute it as the isolated replay user with the fixture socket.
bridge_rev=dff31701d02c63e24b536db6ab7e9d70a7ee3749
bridge_sha=c233f3f0784b8ba2b344c07b76fe0b8d066fcba7b31a9ab3bd90a082bc4c2a7e
curl --disable -fsSL --proto '=https' --proto-redir '=https' --retry 3 --retry-all-errors \
    "https://raw.githubusercontent.com/dmitriiweb/omarchy-agterm/$bridge_rev/scripts/agterm_bridge.py" \
    -o "$work/agterm_bridge.py"
(cd "$work" && printf '%s  agterm_bridge.py\n' "$bridge_sha" | sha256sum --check --strict)
install -m644 "$work/agterm_bridge.py" "$work/replay/agterm_bridge.py"
as_user dbus-run-session -- xvfb-run -a python3 -I "$suite/replay.py" installed "$work/replay"

cp -a "$home" "$work/home-before-removal"
pacman -R --noconfirm agterm-omarchy-bin 2>&1 | tee "$work/remove.log"
[[ "$(grep -Fc '/usr/bin/agtermctl integration uninstall hooks --only claude' "$work/remove.log")" == 1 ]]
diff -r --no-dereference "$work/home-before-removal" "$home"
as_user python3 -I "$suite/replay.py" removed "$work/replay"
