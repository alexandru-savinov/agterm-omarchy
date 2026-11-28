#!/bin/bash
# Inspects a built agterm-omarchy-bin package inside the omarchy-pkgs builder
# image, as root, for the #466 checks. Usage: inspect.sh PKGFILE OUTDIR
set -euo pipefail
pkg=$1 out=$2
mkdir -p "$out"
results="$out/checks.tsv"
: >"$results"
failed=0
record() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" | tee -a "$results"; [[ $1 == PASS ]] || failed=1; }

# pacman installs the numeric owners a package records.
bsdtar --numeric-owner -tvf "$pkg" | awk '{print $3":"$4}' | sort | uniq -c >"$out/owners.txt"
if awk '$2 != "0:0" {bad=1} END {exit bad}' "$out/owners.txt"; then
  record PASS ownership "every entry 0:0 ($(awk '{n+=$1} END {print n}' "$out/owners.txt") entries)"
else
  record FAIL ownership "$(tr '\n' ' ' <"$out/owners.txt")"
fi

bsdtar -xOf "$pkg" .PKGINFO >"$out/PKGINFO"
sed -n 's/^depend = //p' "$out/PKGINFO" | sed 's/[<>=].*//' | sort -u >"$out/depends.txt"

pacman -Sy --noconfirm >/dev/null
pacman -U --noconfirm "$pkg" >"$out/pacman-U.log" 2>&1
pacman -S --needed --noconfirm namcap desktop-file-utils >/dev/null

if bsdtar -tf "$pkg" .INSTALL >/dev/null 2>&1 && grep -qF 'agtermctl integration omarchy enable' "$out/pacman-U.log"; then
  record PASS install-message "post_install printed the integration hint"
else
  record FAIL install-message "no .INSTALL or no hint in pacman-U.log"
fi

declare -A owner=()
ceiling=GLIBC_2.38
max_glibc=GLIBC_2.0
: >"$out/elf.tsv"
while IFS= read -r -d '' f; do
  [[ "$(head -c4 "$f" | od -An -tx1 | tr -d ' ')" == 7f454c46 ]] || continue
  runpath=$(readelf -d "$f" | sed -n 's/.*(RUNPATH).*\[\(.*\)\]/\1/p')
  glibc=$(readelf -V --wide "$f" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1)
  printf '%s\t%s\t%s\n' "${f#/usr/lib/agterm/}" "${glibc:-none}" "${runpath:-none}" >>"$out/elf.tsv"
  [[ -n $glibc ]] && max_glibc=$(printf '%s\n' "$max_glibc" "$glibc" | sort -V | tail -1)
  while read -r soname; do
    [[ -e /usr/lib/agterm/lib/$soname ]] && continue
    owner[$soname]=$(pacman -Qqo "/usr/lib/$soname" 2>/dev/null || echo UNOWNED)
  done < <(readelf -d "$f" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
done < <(find /usr/lib/agterm -type f -print0)

if [[ $(printf '%s\n' "$max_glibc" "$ceiling" | sort -V | tail -1) == "$ceiling" ]]; then
  record PASS glibc-ceiling "highest $max_glibc <= $ceiling"
else
  record FAIL glibc-ceiling "highest $max_glibc > $ceiling: $(awk -F'\t' -v g="$max_glibc" '$2==g {print $1}' "$out/elf.tsv" | tr '\n' ' ')"
fi

for soname in "${!owner[@]}"; do printf '%s\t%s\n' "$soname" "${owner[$soname]}"; done | sort >"$out/needed.tsv"
cut -f2 "$out/needed.tsv" | sort -u >"$out/needed-owners.txt"
missing=$(comm -23 "$out/needed-owners.txt" "$out/depends.txt" | tr '\n' ' ')
# bash and python run the launchers and the bundled agent scripts, not ELF code.
extra=$(comm -13 "$out/needed-owners.txt" "$out/depends.txt" | grep -vxE 'bash|python' | tr '\n' ' ' || true)
if [[ -z $missing && -z $extra ]]; then
  record PASS needed-vs-depends "$(wc -l <"$out/needed-owners.txt") owning packages, all in depends; bash and python are script runtimes"
else
  record FAIL needed-vs-depends "not in depends: ${missing:-none}; in depends but not needed: ${extra:-none}"
fi

if grep -q '/__w/' "$out/elf.tsv"; then
  record FAIL runpath "a RUNPATH names the CI checkout: $(awk -F'\t' '$3 ~ /\/__w\// {print $1}' "$out/elf.tsv" | tr '\n' ' ')"
else
  record PASS runpath "no RUNPATH names the CI checkout"
fi

# The launchers are symlinked into /usr/bin; LD_LIBRARY_PATH is theirs to set.
if ldd_out=$(LD_LIBRARY_PATH=/usr/lib/agterm/lib ldd -r /usr/lib/agterm/bin/agterm-linux.bin /usr/lib/agterm/bin/agtermctl.bin /usr/lib/agterm/bin/zmx 2>&1) \
  && ! grep -qE 'not found|undefined symbol' <<<"$ldd_out"; then
  record PASS ldd "app, CLI and zmx resolve every library and symbol"
else
  record FAIL ldd "$(grep -E 'not found|undefined symbol' <<<"$ldd_out" | sort -u | head -5 | tr '\n' ' ')"
fi
if help=$(/usr/bin/agtermctl --help 2>&1); then
  record PASS cli "/usr/bin/agtermctl --help: $(head -1 <<<"$help")"
else
  record FAIL cli "/usr/bin/agtermctl --help: $(head -1 <<<"$help")"
fi

desktop=/usr/share/applications/io.github.melonamin.agterm.desktop
if ! desktop-file-validate "$desktop" >"$out/desktop-file-validate.txt" 2>&1; then
  record FAIL desktop-file-validate "$(tr '\n' ' ' <"$out/desktop-file-validate.txt")"
elif [[ -s "$out/desktop-file-validate.txt" ]]; then
  # Valid, with hints; the desktop file comes from the release archive (#462).
  printf 'WARN\tdesktop-file-validate\t%s\n' "$(tr '\n' ' ' <"$out/desktop-file-validate.txt")" | tee -a "$results"
else
  record PASS desktop-file-validate "$desktop"
fi

namcap "$pkg" >"$out/namcap-package.txt" 2>&1 || true
namcap -i "/pkgbuild/PKGBUILD" >"$out/namcap-pkgbuild.txt" 2>&1 || true
errors=$(cat "$out/namcap-package.txt" "$out/namcap-pkgbuild.txt" | grep ' E: ' || true)
if [[ -z $errors ]]; then
  record PASS namcap "no errors; warnings in namcap-*.txt"
else
  record FAIL namcap "$(tr '\n' ' ' <<<"$errors")"
fi

exit "$failed"
