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

This tool does not attest or publish a release, or by itself complete feature inventory S03.
Its tests use disposable fixture bytes, not application binaries.
An actual matching build/payload comparison and review are required before claiming build-resolved native coverage.

## Hosted same-build evidence

Omarchy CI calls the existing source SBOM workflow for its checkout commit, preserving its pinned Swift
package check. Each GTK architecture downloads that run's source document, requires the reported commit
to equal its actual checkout (including the synthetic merge commit on pull requests), then augments the
validated stage. Source SBOM failure also fails the aggregate CI gate.

After archiving the tested runtime, a separate `native-sbom-<architecture>-<run>-<attempt>` artifact
contains `sbom.spdx.json`, `native.spdx.json` and `provenance.json`. The latter records the checkout,
architecture, repository, run and attempt plus SHA256 hashes of both documents and the exact runtime
archive uploaded separately. The runtime archive's contents are unchanged.

This wiring requires a successful same-run result on both architectures and comparison with the downloaded
payload before claiming executed coverage. It does not establish native Omarchy runtime acceptance or
per-symbol linker provenance; source associations and observed executable hashes retain the limits above.
