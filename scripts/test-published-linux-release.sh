#!/usr/bin/env bash
# Fixture coverage for the public Linux release verifier.
set -euo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
VERIFY="$ROOT/scripts/verify-published-linux-release.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/agterm-linux-release-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
FIXTURE="$WORK/fixture"

payloads=(
  agterm-linux-v0.14.0-x86_64.tar.gz
  agterm-linux-v0.14.0-x86_64.deb
  agterm-linux-v0.14.0-x86_64.rpm
  agterm-v0.14.0-x86_64.AppImage
)

# The fake gh accepts only provenance bound to the expected repository, its release workflow, and a
# tag that names the artifact being verified.
cat > "$WORK/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
repo="${AGTERM_TEST_EXPECT_REPO:-melonamin/agterm-linux}"
(( $# == 9 ))
[[ "$1 $2" == "attestation verify" ]]
[[ "$4" == "--repo" && "$5" == "$repo" ]]
[[ "$6" == "--signer-workflow" && "$7" == "$repo/.github/workflows/release-linux.yml" ]]
[[ "$8" == "--source-ref" && "$9" == refs/tags/linux-v* ]]
# The tag's version must appear in the artifact name: "-vVERSION" for the tar/DEB/RPM/SBOM/manifest,
# "-VERSION-" for the Arch package (agterm-linux-bin-VERSION-1-ARCH.pkg.tar.zst).
tag_version="${9#refs/tags/linux-v}"
[[ "$(basename "$3")" == *"-v${tag_version}"[-.]* || "$(basename "$3")" == *"-bin-${tag_version}-"* ]]
[[ "$(basename "$3")" != "${AGTERM_TEST_FAIL_ASSET:-}" ]]
EOF
chmod 0755 "$WORK/gh"

# Historical cases describe upstream releases, so they never inherit the caller's GITHUB_REPOSITORY
# (always set on GitHub Actions, including during a tagged release of this repository).
unset GITHUB_REPOSITORY

make_fixture() {
  rm -rf "$FIXTURE"
  mkdir -p "$FIXTURE"
  for artifact in "${payloads[@]}"; do
    printf 'fixture for %s\n' "$artifact" > "$FIXTURE/$artifact"
  done
  (
    cd "$FIXTURE"
    sha256sum "${payloads[@]}" > agterm-linux-v0.14.0-SHA256SUMS
  )
}

must_fail() {
  if "$@" >/dev/null 2>&1; then
    echo "command unexpectedly succeeded: $*" >&2
    exit 1
  fi
}

make_fixture
AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.14.0 >/dev/null

make_fixture
rm "$FIXTURE/${payloads[0]}"
must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.14.0

make_fixture
printf 'unexpected\n' > "$FIXTURE/debug.log"
must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.14.0

make_fixture
printf 'tampered\n' >> "$FIXTURE/${payloads[1]}"
must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.14.0

make_fixture
must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  AGTERM_TEST_FAIL_ASSET="${payloads[2]}" "$VERIFY" linux-v0.14.0

rm -rf "$FIXTURE"
mkdir -p "$FIXTURE"
current_payloads=(
  agterm-linux-v0.33.1-x86_64.tar.gz
  agterm-linux-v0.33.1-x86_64.deb
  agterm-linux-v0.33.1-x86_64.rpm
)
for artifact in "${current_payloads[@]}"; do
  printf 'fixture for %s\n' "$artifact" > "$FIXTURE/$artifact"
done
(
  cd "$FIXTURE"
  sha256sum "${current_payloads[@]}" > agterm-linux-v0.33.1-SHA256SUMS
)
AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.33.1 >/dev/null
printf 'unexpected\n' > "$FIXTURE/agterm-v0.33.1-x86_64.AppImage"
must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.33.1

# A fixture with the three packages and the checksum manifest for VERSION, and nothing else.
make_package_fixture() {
  local version="$1"
  local packages=(
    "agterm-linux-v${version}-x86_64.tar.gz"
    "agterm-linux-v${version}-x86_64.deb"
    "agterm-linux-v${version}-x86_64.rpm"
  )
  rm -rf "$FIXTURE"
  mkdir -p "$FIXTURE"
  for artifact in "${packages[@]}"; do
    printf 'fixture for %s\n' "$artifact" > "$FIXTURE/$artifact"
  done
  (
    cd "$FIXTURE"
    sha256sum "${packages[@]}" > "agterm-linux-v${version}-SHA256SUMS"
  )
}

# Published releases after 0.33.1+linux.1 that shipped without an SBOM. These are the exact asset
# names `gh release view` lists for them on melonamin/agterm-linux, and they must keep verifying.
for version in 0.39.1 0.39.1+linux.1 0.40.0; do
  make_package_fixture "$version"
  AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version" >/dev/null
done

# Every version after the cutoff must carry an attested SBOM.
for version in 0.41.1+linux.1 0.42.0; do
  make_package_fixture "$version"
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version"
  printf '{}\n' > "$FIXTURE/agterm-linux-v${version}.spdx.json"
  AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version" >/dev/null
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    AGTERM_TEST_FAIL_ASSET="agterm-linux-v${version}.spdx.json" "$VERIFY" "linux-v$version"
done

# A verifier run on Actions (or by a user who exports GITHUB_REPOSITORY) authenticates against that
# repository, and provenance from any other repository is rejected.
make_package_fixture 0.42.0
printf '{}\n' > "$FIXTURE/agterm-linux-v0.42.0.spdx.json"
GITHUB_REPOSITORY=example/agterm-fork AGTERM_TEST_EXPECT_REPO=example/agterm-fork \
  AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" "$VERIFY" linux-v0.42.0 >/dev/null
must_fail env GITHUB_REPOSITORY=example/agterm-fork AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
  "$VERIFY" linux-v0.42.0

# Prereleases use the same artifact contract and tag binding.
make_package_fixture 0.42.0-rc.1
printf '{}\n' > "$FIXTURE/agterm-linux-v0.42.0-rc.1.spdx.json"
AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" "$VERIFY" linux-v0.42.0-rc.1 >/dev/null

echo "→ public release verifier accepts the authenticated historical and current artifact sets"
for version in 0.40.0 0.40.0+linux.1; do
  rm -rf "$FIXTURE"
  mkdir -p "$FIXTURE"
  payloads=(
    "agterm-linux-v${version}-x86_64.tar.gz"
    "agterm-linux-v${version}-x86_64.deb"
    "agterm-linux-v${version}-x86_64.rpm"
    "agterm-linux-bin-${version}-1-x86_64.pkg.tar.zst"
  )
  manifest="agterm-linux-v${version}-SHA256SUMS"
  for artifact in "${payloads[@]}"; do
    printf 'fixture for %s\n' "$artifact" > "$FIXTURE/$artifact"
  done
  (cd "$FIXTURE" && sha256sum "${payloads[@]}" > "$manifest")
  for option in '' --require-arch; do
    args=("linux-v$version")
    [[ -z "$option" ]] || args+=("$option")
    AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" "$VERIFY" "${args[@]}" >/dev/null
  done
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    AGTERM_TEST_FAIL_ASSET="${payloads[3]}" "$VERIFY" "linux-v$version" --require-arch
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    AGTERM_TEST_FAIL_ASSET="$manifest" "$VERIFY" "linux-v$version" --require-arch
  printf 'tampered\n' >> "$FIXTURE/${payloads[3]}"
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version" --require-arch
  rm "$FIXTURE/${payloads[3]}"
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version"
  (cd "$FIXTURE" && sha256sum "${payloads[@]:0:3}" > "$manifest")
  must_fail env AGTERM_GH="$WORK/gh" AGTERM_RELEASE_DOWNLOAD_DIR="$FIXTURE" \
    "$VERIFY" "linux-v$version" --require-arch
done

echo "→ public release verifier accepts authenticated historical, Arch, and revision artifact sets"
