#!/usr/bin/env bash
set -euo pipefail

(( $# > 0 )) || { echo 'usage: ci-install-ubuntu-deps.sh PACKAGE...' >&2; exit 2; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_CODENAME" == noble ]] || {
  echo 'CI dependencies require Ubuntu Noble' >&2
  exit 1
}
case "$(dpkg --print-architecture)" in
  amd64) mirror=https://mirrors.edge.kernel.org/ubuntu ;;
  arm64) mirror=https://mirrors.mit.edu/ubuntu-ports ;;
  *) echo 'Unsupported CI package architecture' >&2; exit 1 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"
mkdir "$work/lists"
cat > "$work/ubuntu.sources" <<EOF
Types: deb
URIs: $mirror
Suites: noble noble-updates noble-security noble-backports
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
options=(
  -o "Dir::Etc::sourcelist=$work/ubuntu.sources"
  -o Dir::Etc::sourceparts=-
  -o "Dir::State::lists=$work/lists"
  -o APT::Update::Error-Mode=any
)
apt-get "${options[@]}" update
DEBIAN_FRONTEND=noninteractive apt-get "${options[@]}" install -y --no-install-recommends "$@"
