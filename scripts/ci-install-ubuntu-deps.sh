#!/usr/bin/env bash
set -euo pipefail

(( $# > 0 )) || { echo 'usage: ci-install-ubuntu-deps.sh PACKAGE...' >&2; exit 2; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_CODENAME" == noble ]] || {
  echo 'CI dependencies require Ubuntu Noble' >&2
  exit 1
}
case "$(dpkg --print-architecture)" in
  amd64) mirrors=(https://mirrors.edge.kernel.org/ubuntu https://mirrors.mit.edu/ubuntu) ;;
  arm64) mirrors=(https://mirrors.mit.edu/ubuntu-ports) ;;
  *) echo 'Unsupported CI package architecture' >&2; exit 1 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"
for mirror in "${mirrors[@]}"; do
  # A failed update may leave partial indexes; never offer those to the next source or install.
  attempt=$(mktemp -d "$work/attempt.XXXXXX")
  chmod 755 "$attempt"
  mkdir "$attempt/lists"
  cat > "$attempt/ubuntu.sources" <<EOF
Types: deb
URIs: $mirror
Suites: noble noble-updates noble-security noble-backports
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
  options=(
    -o "Dir::Etc::sourcelist=$attempt/ubuntu.sources"
    -o Dir::Etc::sourceparts=-
    -o "Dir::State::lists=$attempt/lists"
    -o APT::Update::Error-Mode=any
  )
  echo "Updating CI dependency indexes from $mirror" >&2
  if apt-get "${options[@]}" update; then
    # Do not retry installation: a failure may follow package configuration side effects.
    DEBIAN_FRONTEND=noninteractive apt-get "${options[@]}" install -y --no-install-recommends "$@"
    exit 0
  else
    status=$?
    echo "CI dependency index update failed for $mirror (exit $status)" >&2
  fi
done
echo 'No CI dependency source completed a verified index update' >&2
exit "$status"
