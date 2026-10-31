#!/usr/bin/env bash
# Record the runner CPU identity and execute a payload's bundled zmx directly,
# natively and under qemu-aarch64 user-mode CPU models, in a private ZMX_DIR.
# Usage: cpu-zmx-probe.sh <zmx-binary> <output-dir>
# Writes cpu.txt, zmx-results.tsv (label, step, exit code, signal) and per-step logs.
# Never fails on a probe failure: results are data. Fails only on usage errors.
set -uo pipefail

zmx_bin="${1:?zmx binary}"
out="${2:?output dir}"
mkdir -p "$out/logs" "$out/cores"
zmx_bin="$(readlink -f "$zmx_bin")"
results="$out/zmx-results.tsv"
printf 'label\tstep\texit\tsignal\tnote\n' > "$results"

{
  echo "## uname -a"; uname -a
  echo "## /etc/os-release"; grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release
  echo "## nproc"; nproc
  echo "## /proc/cpuinfo (processor 0)"
  awk 'BEGIN{RS=""} NR==1{print; exit}' /proc/cpuinfo
  echo "## distinct Features lines across all processors"
  grep -E '^Features' /proc/cpuinfo | sort | uniq -c
  echo "## distinct CPU implementer/part/variant/revision"
  grep -E '^CPU (implementer|architecture|variant|part|revision)' /proc/cpuinfo | sort | uniq -c
  echo "## lscpu"; lscpu 2>/dev/null || true
  echo "## AT_HWCAP (LD_SHOW_AUXV)"; LD_SHOW_AUXV=1 /bin/true 2>/dev/null | grep -E 'AT_HWCAP|AT_PLATFORM' || true
  echo "## zmx file"; sha256sum "$zmx_bin"; file "$zmx_bin" 2>/dev/null || true
} > "$out/cpu.txt" 2>&1

signal_of() {
  # timeout(1) and bash report death-by-signal as 128+N; 124 is the timeout itself.
  local code="$1"
  if [[ "$code" == 124 ]]; then echo "TIMEOUT"
  elif (( code > 128 && code < 160 )); then
    local name; name="$(kill -l $((code - 128)) 2>/dev/null || echo "?")"; echo "SIG${name}"
  else echo "-"; fi
}

# run_step <label> <step> <runner...> -- <zmx args...>
run_step() {
  local label="$1" step="$2"; shift 2
  local runner=()
  while [[ "$1" != "--" ]]; do runner+=("$1"); shift; done
  shift
  local log="$out/logs/${label}--${step}.log" code
  {
    echo "\$ ${runner[*]} zmx $*"
    timeout --kill-after=5 20 "${runner[@]}" "$zmx_bin" "$@"
  } > "$log" 2>&1 < /dev/null
  code=$?
  local sig; sig="$(signal_of "$code")"
  local note; note="$(grep -m1 -E 'uncaught target signal|Illegal instruction|killed session|error' "$log" | tr '\t' ' ' | cut -c1-160)"
  printf '%s\t%s\t%s\t%s\t%s\n' "$label" "$step" "$code" "$sig" "${note:--}" >> "$results"
  echo "[$label] $step -> exit=$code signal=$sig"
}

probe() {
  # probe <label> [runner...]
  local label="$1"; shift
  local runner=("$@")
  [[ ${#runner[@]} -eq 0 ]] && runner=(env)
  local dir; dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/zmx-probe-XXXXXXXX")"
  chmod 700 "$dir"
  local name="ultra-probe-${label//[^a-z0-9]/-}-$$"
  (
    export ZMX_DIR="$dir"
    unset ZMX_SESSION ZMX_SESSION_PREFIX
    cd "$out/cores" || exit 1
    ulimit -c unlimited 2>/dev/null || true
    run_step "$label" version "${runner[@]}" -- version
    run_step "$label" help "${runner[@]}" -- help
    run_step "$label" run-create "${runner[@]}" -- run "$name" -d sleep 120
    sleep 1
    run_step "$label" list "${runner[@]}" -- list
    run_step "$label" kill "${runner[@]}" -- kill "$name" --force
    run_step "$label" list-after-kill "${runner[@]}" -- list
  )
  # Belt and braces: kill any probe daemon left in this private directory with the native binary.
  ZMX_DIR="$dir" timeout 10 "$zmx_bin" kill "$name" --force > /dev/null 2>&1 || true
  pkill -f -- "$dir" > /dev/null 2>&1 || true
  rmdir "$dir" 2>/dev/null || ls -la "$dir" > "$out/logs/${label}--leftover-zmx-dir.txt" 2>&1
}

probe native

if command -v qemu-aarch64 > /dev/null; then
  qemu-aarch64 --version > "$out/qemu-version.txt" 2>&1
  qemu-aarch64 -cpu help > "$out/qemu-cpu-models.txt" 2>&1
  for model in ${QEMU_CPU_MODELS:-max max,sve=off max,sve=off,sme=off neoverse-n2 neoverse-v1 neoverse-n1 cortex-a76 cortex-a72}; do
    if ! grep -qw -- "${model%%,*}" "$out/qemu-cpu-models.txt"; then
      printf '%s\t%s\t%s\t%s\t%s\n' "qemu-$model" "all" "-" "-" "NOT RUN: model not offered by this qemu" >> "$results"
      continue
    fi
    probe "qemu-$model" qemu-aarch64 -cpu "$model"
  done
else
  printf 'qemu\tall\t-\t-\tNOT RUN: qemu-aarch64 not installed\n' >> "$results"
fi

# qemu-user writes guest core files (qemu_<prog>_<time>_<pid>.core) for uncaught signals.
# Decode the faulting PC/instruction/function with gdb when one exists.
shopt -s nullglob
for core in "$out"/cores/*.core "$out"/cores/core*; do
  {
    echo "## $(basename "$core")"
    # shellcheck disable=SC2016  # $pc is a gdb expression, not a shell one
    gdb -nx -batch -ex 'info registers pc' -ex 'x/4i $pc' -ex 'bt 12' "$zmx_bin" "$core" 2>&1 | grep -vE '^(warning: |\[New LWP)' | head -60
  } >> "$out/fault-decode.txt"
done
# Core files are large and contain process memory of a throwaway probe only; keep the decode, drop cores.
rm -f "$out"/cores/*.core "$out"/cores/core* 2>/dev/null
rmdir "$out/cores" 2>/dev/null || true

column -t -s $'\t' "$results" || cat "$results"
exit 0
