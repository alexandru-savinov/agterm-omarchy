# Native installer fixture evidence

This additive extract records unchanged-source native Python installer, staging
and rollback fixtures at
[source77](https://github.com/alexandru-savinov/agterm-omarchy/commit/77cce43eb791ba1a983c19760c7df5259d0e452d).
The completed attempt passed all 46 tests, exit 0.
The earlier preparation failure remains separate.
The [original operator report](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6809759412)
records these outcomes before this extract was prepared.

The original driver was initially named operator.py, shadowing Python's
standard operator module during imports.
Its retained traceback precedes fixture/service execution; no status or test log
was produced, and a separate operator exit code was not retained.
The launch/result collection commands' zero exits are not fixture results.
The corrected attempt used the same driver bytes under a different filename.

The completed log contains 10 PayloadStageTests, 19 PersonalInstallTests
and 17 InstallerSafetyTests, each explicitly ok.
All 46 identifiers match definitions in the two hash-bound source modules.
The unittest interval is 5.701 seconds, the service-reported runtime is
5.775 seconds, and the operator's measured service invocation is
5.805122185000073 seconds.

The recorded transient service used RuntimeMaxSec=180, TimeoutStopSec=5
and KillMode=control-group; the operator's subprocess wait limit was 210 seconds.
The normal-user worker observed both source and actual installation read-only.
The recorded preservation checks cover the source, installed and staged files,
protected application process and login shell.
Afterward the service was collected/inactive, its cgroup was absent and the
private temporary-fixture directory had no remaining entries.
Retained evidence stages are not claimed deleted.
These are historical observations; this preparation performed no new inspection
of the VM, running applications or installed helpers.

native-installer-fixtures.json contains selected record fields, complete
sanitized arguments, the actual test names/results and explicit scope limits.
original-bindings.json binds all five original records/driver and all 102
recorded source inputs; source copies were also checked against the official
public Git tree during preparation.
The verifier rehashes those copies against the retained native input inventory.
It does not query the network or guest to refresh historical observations.
log-projections.json contains all three decoded log streams, with explicit
original UTF-8 byte hashes and reversible path/unit/invocation aliases.
Raw process, namespace, user/group and owner details are omitted from the selected
records and remain in their byte-bound originals.
The alias map is not published; the verifier reconstructs replacements from
original records and proves the log round trips in memory.

Run the read-only verifier against the retained validation root and the prepared
source77 copy (or another identical source tree):

~~~sh
python3 -B verify-native-installer.py \
  --original-root "$ORIGINALS" --source-root "$SOURCE_COPY"
~~~

It reads bytes and parses ASTs without importing or executing original code.
It verifies public file hashes, original records, source inputs, driver bounds,
the 46 actual results, complete log transformations and preservation/cleanup
fields, and rejects seven in-memory corruption controls.
It performs no compiler, app/helper, VM or fixture execution and writes no files.

Root may publish the five files listed in manifest.json plus the manifest
under a fresh additive evidence directory after local review.
Keep earlier evidence packages and frozen PR #317 unchanged.
These synthetic-payload tests complement the separate real-payload installation;
they establish neither graphical behavior nor release acceptance.
Same-account preparation and verification are not independent approval.
