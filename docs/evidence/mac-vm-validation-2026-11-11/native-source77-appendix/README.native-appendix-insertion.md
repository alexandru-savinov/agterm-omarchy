### Addendum: exact native logs and separate renderer experiments

This appendix adds retained evidence for source `77cce43eb791ba1a983c19760c7df5259d0e452d`.
It does not rerun tests or change the earlier native evidence package. Its frozen
manifest remains `34bce9ff0f5c61ecafe95670b139d34566eb31e8008b5e1ea8c65d69480dbffc`.

Root recovered the three original guest log files through binary-safe transfer.
The original command records, recovered byte counts and SHA256 hashes agree:

| Log | Raw bytes | Literal test passes / failures | Raw SHA256 |
| --- | ---: | ---: | --- |
| Frontend attempt 2 | 346178 | 692 / 1 | `3ec99f3eb35a435f5eb51de1fed8648062103c9e509b0700d3a1cb277ad34526` |
| Frontend attempt 3 | 166173 | 693 / 0 | `8ea75f1ea7b0c41dc29065f273f2fee3e74e041f5d50becbff10fc6c5863ca23` |
| Core | 1660011 | 4104 / 0 | `1e9dfc9a3b2a79ca90103c50e2a2b63faf2d2b0955c1bb1d432068219f561866` |

[The complete third-attempt observations](native-attempt3-complete.json) now include
the entire decoded log with declared identifier aliases, retaining original CR/LF.
Its literal recount is 693 passed tests, 99 passed suites, zero failed tests/suites
and zero skips. It reused compiled tests with `--skip-build`, the same 11 source
inputs and the same compiler hashes as attempt 2; it is not a new rebuild result.
The earlier `pathTooLong` failure and first-attempt setup failure remain historical.

[The recovery comparison](native-log-recovery.json) proves the earlier attempt 2
and core text equals the raw UTF-8 text after universal newline normalization.
One and two CRLF pairs respectively explain the byte-size differences; bare CR
characters were also converted to LF without changing byte length. All recovered
logs are valid UTF-8. Core's one U+FFFD was already present in the original bytes.
Core retains the explicit missing-fish skip: the framework reports 4105 tests,
while the anchored literal count is 4104 passes and one skip. The transfer manifest's
broad `skip_lines` selection also contains test names and is not a skip count.
The first text-mode comparison is retained as superseded history. Earlier package
collection-limit statements describe what was available then; this addendum resolves
those collection gaps without editing that package or repeating older complete logs.

[The default-renderer experiment](native-default-renderer.json) failed before any
ordinary checks completed: GTK could not create an EGL context. All seven renderer
launch overrides were absent. The exact three GL diagnostic lines are retained;
unfiltered Fontconfig/system output remains private with an explicit projection
hash, size and omitted-line count. Transport success and successful cleanup do not
turn this failed runtime experiment into a pass.

[The separate software-installer experiment](native-software-installer-renderer.json)
used the exact source77 installer with `--software-rendering` in a private prefix.
It preserved 769 original payload files and added only the two installer metadata
files. Its ordinary native Wayland harness passed all seven checks. Deliberate
launcher input `LIBGL_ALWAYS_SOFTWARE` value `0` became kernel-observed value `1`,
while `AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE` retained `0`. Both renderer experiments
record successful independent cleanup, two preserved protected process identities,
and unchanged original payload, real installation, shared runtime and login shell.
This demonstrates the private software launcher behavior; hardware rendering,
current main, an integrated candidate and trusted hosted release acceptance remain
outside this evidence.

[Bindings](appendix-bindings.json) cover 67 immutable inputs, nine worker/operator
sources and 14 exact Git blobs. [The verifier](verify-native-appendix.py) only reads
files and immutable Git objects. It recomputes hashes/counts/newline comparisons,
reverses declared aliases to compare retained observations, scans publication files
for private identifiers, and exercises eight negative controls. Results are in
[verification.json](verification.json); exact deliverable hashes are in
[package-manifest.json](package-manifest.json). This is same-account preparation,
not independent approval or a new runtime gate.

For root's local recheck, supply the unchanged private evidence root and a local
repository containing the pinned commits:

```sh
python3 -B verify-native-appendix.py --original-root "$PRIVATE_EVIDENCE_ROOT" --repository "$SOURCE_REPOSITORY"
```

The default command is read-only. Root owns integration into #317 and publication.
