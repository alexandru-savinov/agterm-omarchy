# Supplementary native fish and Mac policy evidence

This additive extract keeps five distinct native observations: the accidental
help invocation, the later listing, the selected fixture, its original reporting
failure, and a separate read-only recount.
It also records five Mac policy assessments.
No runtime, compiler, installed helper or VM operation was repeated to prepare it.

## Native fish

All three invocations bind unchanged
[source77](https://github.com/alexandru-savinov/agterm-omarchy/commit/77cce43eb791ba1a983c19760c7df5259d0e452d)
and the same recorded test-binary,
source-fixture and private fish-image digests.
The private fish 4.9.3-1 preparation has successful archive-digest and signature
receipts; its package, signature and keyring identities remain explicit.
The preparation itself executed no package binary.
The subsequent isolated services did execute fish for their version and fixture
checks; fish was not installed globally or made the real login shell.
The [native report](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6810769894)
and [Mac assessment report](https://github.com/alexandru-savinov/agterm-omarchy/issues/305#issuecomment-6810850361)
precede this extract.

| Observation | Retained result |
|---|---|
| Accidental help invocation | Swift Testing began the full selection. The 45-second child deadline expired; service exit 1, original aggregate false and no complete suite result. |
| Listing | 4,105 identifiers, exactly one requested fish identifier, no test-body events; service exit 0. |
| Selected fixture | One literal test and one suite pass in 0.031 seconds; service exit 0 and no failure or skip. |
| Original selected-run aggregate | False. Its broad pass regex counted the final summary as a second test. This record remains unchanged. |
| Later read-only recount | Confirms one literal start/pass/summary and preservation against the original log; no runtime retry. |

The failed invocation's collected/inactive service and absent cgroup are distinct
from temporary-fixture cleanup: its final record contains 1,134 temporary
remainders (including test directories and one job PID file).
Their count, categories and canonical-list binding are preserved.
The later listing and selected fixture each record no temporary remainders.
The subsequent administrative check found the failed service already inactive;
root reported sending no signal.

Each native service had RuntimeMaxSec=90, TimeoutStopSec=5 and a 115-second
operator wait bound; the core child deadline was 45 seconds and fish-version
deadline 10 seconds.
Read-only source/installation/fish mount observations, private namespace
comparisons, cgroup state and all recorded source/payload/private-package,
protected-app, login-shell, global-fish and real /usr/local preservation fields
remain separate for each attempt.
These are historical observations, not a new inspection of guest state.

One original-byte limitation is explicit: the failed invocation's decoded core
log does not reproduce its separately recorded raw byte count/hash.
The retained decoded text and original raw receipt are bound separately.
The discrepancy is not repaired or treated as a byte-identical raw-log recovery.
Its partial pass events do not establish a complete core-suite pass.
The listing, selected-fixture and service logs do match their recorded raw bytes.

## Mac policy assessments

Every assessment used a 30-second bound and completed within that bound.
The exact per-command elapsed times and ten stdout/stderr log bindings are retained.

| Target | Exit | Observation |
|---|---:|---|
| Standalone zmx image | 3 | Valid code that does not appear to be an app. |
| Pristine bundle | 0 | Accepted as Notarized Developer ID. |
| Pristine-bundle zmx image | 3 | Same not-an-app diagnostic. |
| Bundle with disclosed peer addition | 1 | Missing or invalid sealed resource. |
| Augmented-bundle zmx image | 3 | Same not-an-app diagnostic. |

These were policy assessments, not launches of the target helper and not
administrative-stop operations.
All three images received the same direct-image assessment, including the
standalone context which previously executed successfully.
The results do not establish the cause of prior bundle-context startup delays.
Original timeouts and missing stopped-reply acceptance remain unchanged.
Fresh-context, installed-tree/attribute, original-package and protected-process
preservation are recorded historical results; retained stages are not claimed
deleted.

## Files and verification

native-fish.json and mac-policy.json are explicit selected-record projections.
original-bindings.json binds all original records, drivers and assessment logs,
the source fixture and the original context manifest.
The verifier also rehashes that manifest's 2,065 input entries and the three
retained context images; it never imports the original diagnostic helpers.
Native guest binary/package digests are cross-checked historical receipts, not
fresh reads of those guest files.

log-projections.json identifies complete streams versus selected line numbers.
The large accidental log and listing are intentionally projected, with their
full captured-text bindings retained.
Mac publisher-identity lines are omitted explicitly.
Selected private paths and unit names use aliases which the verifier reverses
against originals in memory; raw process/namespace/owner values are omitted.

~~~sh
python3 -B verify-fish-policy.py \
  --original-root "$ORIGINALS" --source-root "$SOURCE_COPY"
~~~

The verifier reads bytes and parses source ASTs without executing original code.
It checks all public file hashes, record/log/source relationships, literal
selection and recount, preservation/cleanup and assessment boundaries.
Ten in-memory corruption controls reject missing/extra test events, a missing
summary, failed service/input bindings, erased historical failures/remainders
and invented Mac acceptance or relaxed assessment deadlines.
The original recount's own five recorded controls are retained separately.

Root may publish only the six manifest-listed files plus manifest.json into a
fresh additive evidence directory after local review.
Original records, other evidence packages and frozen PR #317 remain unchanged.
The supplementary single test does not relabel the earlier full-core result or
certify an evolving candidate.
Same-account preparation and verification are not independent approval.
