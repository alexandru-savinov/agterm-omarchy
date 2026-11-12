# Mac administrative-stop prerequisite and context diagnostics

This is an additive historical extract for publication separately from frozen PR #317.
It records failed prerequisites and a completed diagnostic comparison.
**No host administrative-stop request or stopped reply was reached.**
It establishes neither a successful administrative stop nor a failure of that product operation.
No runtime or compiler test was rerun to prepare this extract.

| Observation | Operator observation bound | Retained outcome |
|---|---|---|
| Synthetic bundle, `root-stop-01` and `root-stop-02` | 3 seconds each | Initial `zmx list` timed out before host/peer launch; identity-checked SIGTERM, exit -15, complete final inventory with no private survivors. |
| Additional sampled synthetic-bundle diagnostic | 15 seconds; one-second sample with an eight-second sampling-command cap | Timed out at 15.005862 seconds; SIGTERM, exit -15; process gone and namespace empty. Selected sample lines show `_dyld_start` and 96K footprint, without establishing a cause. |
| Earlier standalone observation | A separate driver and configured deadline were not retained in this finite input set | Exit 0 in 0.662008 seconds, exact empty-session inventory output, no signal, process gone. Its `namespace_empty: false` remains unchanged. |
| Complete bundle plus disclosed peer, `root-bundle-stop-01` | 3 seconds for initial inventory | Bundle/metadata/attribute prerequisites passed; inventory still timed out before host/peer launch. Verified SIGTERM, exit -15 and no private survivors; all recorded preservation checks passed. |
| Controlled comparison: standalone | 3 seconds | Exit 0 in 2.630461 seconds; exact empty-session inventory output, no recovery signal. |
| Controlled comparison: pristine complete bundle | 3 seconds | Timeout after 3.022733 seconds; verified SIGTERM, exit -15. |
| Controlled comparison: complete bundle with peer | 3 seconds | Timeout after 3.027684 seconds; verified SIGTERM, exit -15. |

The stop fixtures configured a 15-second stopped-reply budget, but execution never reached it.
Their SIGTERM and fallback SIGKILL recovery budgets were two seconds each; no retained attempt needed SIGKILL.
The comparison likewise used two-second TERM/KILL recovery bounds and collected no new sample.
It ran once in the fixed order standalone, pristine, then augmented.
Timing/cache effects remain possible; these observations do not identify an OS-policy cause.
Because the pristine context also timed out, the peer addition alone does not explain the result.

Every comparison image has zmx SHA256 `4db635396d83ded73e102fb365375a9216c01da515d58c796537b83210b6eb01`.
The pristine copy preserves all 640 original files and 669 tree entries.
The augmented copy adds only `Contents/MacOS/session-host-test-client` and preserves every original entry.
Recorded strict signature checks pass for the pristine aggregate and individual images; the augmented aggregate fails its resource seal.
No image was re-signed and no OS-policy workaround was used.
The retained release comparison binds the installed images to the official v0.38.1 DMG and declared upstream `09727610`; it is not a reproduced source build.
The source-equivalent peer was built at `2ddd93af`.

The comparison observed all three actual image identities, preserved contexts/attributes and 751 earlier input files, preserved the installed bundle/protected process, and left no private process survivors.
These are recorded historical preservation observations, not a new inspection of running applications.
Retained fixture stages and logs are evidence; a successful empty-session inventory does not mean the namespace directory has no files.

The JSON files identify complete-document alias transformations versus selected metadata fields.
`selected-log-projections.json` explicitly records each original hash, line count, selected line number and whether the stream is complete.
Exception tracebacks and sample headers are intentionally partial projections; the raw files remain bound in `original-bindings.json`.
Private stage/bundle identifiers, process/user IDs and load addresses use reversible aliases.
The alias map stays private; the verifier reconstructs it in memory and checks both transformed and reversed observations.

Run the read-only verifier with the retained validation directory supplied as `ORIGINALS`:

```sh
python3 verify-mac-admin-context.py --original-root "$ORIGINALS"
```

It rehashes 2,089 original files, validates actual retained input-tree modes/types/content, joins driver/result/source/image receipts, checks deadlines/signals/failure boundaries, and rejects six in-memory corruptions.
It uses the Python standard library and never imports an original helper, runs an application/compiler, contacts a service, or writes to an original package.
The original standalone result has no separate driver/deadline receipt in this input set; that gap is explicit, and the later driver is never substituted for it.

Root can publish the files listed by `manifest.json` plus the manifest into a new evidence directory and link this note as the additive Mac diagnostic section.
Keep PR #317 and earlier evidence packages unchanged.
Same-account preparation and verification are not independent approval or native release acceptance.
