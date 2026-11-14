# Native SBOM supplement

Run after staging a payload with the matching source checkout and generating the source SPDX 2.3 document:

```sh
python3 scripts/native-sbom.py source.spdx.json staged-payload native.spdx.json
python3 scripts/tests/test_native_sbom.py
```

The output must not already exist.
Existing source packages and relationships are preserved in a new document namespace.
The tool reuses `linux-notices.py` to validate pinned notices and inventory, checks the staged profile,
compares every recorded library checksum and the exact library set, and hashes the mapped executables.
Unknown libraries, missing executables, stale pins, linked files and mismatched profiles fail before output creation.

Native entries distinguish pinned source/notice inputs, input archives and observed staged binaries.
`OTHER` relationships describe notice associations under the [SPDX relationship specification](https://spdx.github.io/spdx-spec/v2.3.1/relationships-between-SPDX-elements/).
They do not assert that every source component survives linking or that arbitrary staged binaries were built from those inputs.
Executable checksums are observations; the existing staging notice metadata does not provide independently expected executable hashes.
The original inventory's unresolved coverage is retained, with licenses marked `NOASSERTION` rather than inferred from filenames.
Source inputs and binary provenance still require the trusted build's separate evidence.

This standalone tool does not change release workflows, attest or publish a release, or complete feature inventory S03.
Its tests use disposable fixture bytes, not application binaries.
An actual matching build/payload comparison and review are required before claiming build-resolved native coverage.
