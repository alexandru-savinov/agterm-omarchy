# Retrospective generated-config storage proposal (#126)

This evidence-only package was prepared on 2026-11-11 to deliver the previously missing #126 artifact.
It is retrospective: the production implementation already arrived independently through #192 and PR #195.
It does not claim that the original prototype was delivered earlier, change active application source, or select a native candidate.

The helper and complete test file are byte-for-byte copies of reviewed commit `41f4f7c29bb7faf961ad75bcfe7fc0f31d013ed3`:

- `Sources/AgtermLinux/GhosttyGeneratedConfigStorage.swift`: Git blob `37ed92f74c3faea82e671efc6d658f849d4b521c`.
- `Tests/AgtermLinuxTests/GhosttyGeneratedConfigStorageTests.swift`: Git blob `13213037f17a5dc86f97b92f2f23774bfc620c37`.

The tiny standalone package has no third-party dependencies, GTK initialization or libghostty linkage.
It gives the existing test import and child compiler the same package-relative source layout as production.
The Linux-only `actualGhosttyReplay` case remains in the copied test source but is explicitly excluded from this standalone run: it needs the pinned vendor library, which this package does not supply.
No test source was weakened or rewritten to avoid that dependency.

## Storage and adoption contract

Each helper owns a fresh `mkdtemp` directory with mode 0700 under the supplied runtime root, or the supplied temporary fallback when the runtime root is missing/empty.
Generated files use exclusive, no-follow creation with mode 0600.
Setup/write errors throw; the consumer never receives a stale path after failure.

Default, runtime and title-reset files remain available throughout their synchronous consumer and are then unlinked, including on a throwing consumer.
One immutable light/dark theme pair remains until all config/surface owners and the Ghostty app are freed.
This is the corrected bounded lifetime from #192: ordinary arguments are copied into pinned libghostty's replay state, while conditional theme finalization can reopen the theme paths.
The 1000-rebuild test retains exactly two files and two bookkeeping entries between consumers.

Cleanup uses the instance's owned directory descriptor and known filenames rather than recursive removal.
It preserves a peer instance and unrelated entries, is idempotent, and prevents subsequent writes.
The helper never changes process-global `XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`, D-Bus or login-shell environment values.
The overlapping-child test gives both fixture processes the same disposable runtime root and verifies that their complete environments stay unchanged while each helper runs.

`adoption.patch` is an **unapplied** four-file diff against the originally requested base `fd85fc5824a782d17b2aee0cb113ef31f9ce66ab`.
It adds the reviewed helper/tests and replaces shared default/runtime/theme paths in `GhosttyApp.swift` with scoped consumers and the retained pair.
The shutdown integration point is `App.swift`'s existing GApplication shutdown handler: after existing persistence/control shutdown, tear down registered window surfaces, free the Ghostty app, then clean its generated files.
The two existing application files at `fd85fc58` are identical to implementation base `8aa04012590e7f1c6918d9d0b6abdea8f771c56a`, so the adoption patch requires no competing design or adaptation.
Do not apply it to current main, where the implementation already exists.

## Replay the host-free tests

Use Swift 6 with a C toolchain and a writable temporary directory.
`swiftc` must be on PATH because the overlapping-process case compiles the exact copied helper into its child fixture.
From the repository root:

```sh
proposal="$PWD/docs/evidence/linux-generated-config-storage-proposal"
scratch="$(mktemp -d)"
swift test --package-path "$proposal" --scratch-path "$scratch/build" --jobs 1 \
  --filter GhosttyGeneratedConfigStorageTests --skip actualGhosttyReplay
```

The expected result is nine passing tests, including permissions, scoped lifetime, 1000 rebuilds, failed consumers, missing-root fallback, invalid roots, exclusive-write failure, owner-only cleanup/replacement handling and two overlapping real child processes.
The source has ten tests on Linux; the excluded tenth is the explicit C API case, not a silent missing-prerequisite skip.
Delete the task-created scratch directory after retaining any required logs.
No frontend, display, terminal, session or VM is launched by these nine tests.

## Check the inert patch without adopting it

Use a repository containing the exact old base and a new temporary index, leaving the caller's working tree and ordinary index alone:

```sh
proposal="$PWD/docs/evidence/linux-generated-config-storage-proposal"
scratch="$(mktemp -d)"
GIT_INDEX_FILE="$scratch/index" git read-tree fd85fc5824a782d17b2aee0cb113ef31f9ce66ab
GIT_INDEX_FILE="$scratch/index" git apply --cached --check "$proposal/adoption.patch"
```

This checks applicability only; it neither adopts nor builds the application patch.
`SHA256SUMS` identifies every replay input and the retained validation transcript; run `sha256sum -c SHA256SUMS` from this directory.

## Evidence and limits

`validation.txt` records this package's actual toolchain, command results and test output.
Those new helper results are distinct from the historical [PR #195 review](https://github.com/alexandru-savinov/agterm-omarchy/pull/195#pullrequestreview-5743510527) and [hosted run 41505996996](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/41505996996), whose real pinned-libghostty replay ran at `41f4f7c2` on both architectures.
The copied helper tests alone do not establish libghostty replay behavior, application shutdown ordering, current-main build success or native conditional-theme reload.

Full Linux build/GTK behavior and exact-artifact native ordinary/Live, two-instance reload/cleanup and noisy-title shutdown acceptance remain separate implementation-owner gates under #192/#199/#4.
Native testing is deferred by the user; no result from this package certifies it.
The old patch is retained for review and reproduction, not as a new release or deployment recommendation.
