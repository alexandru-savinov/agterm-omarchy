# CI correction and review wave: 2026-11-02

[Root opened this wave](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6583604464) after the three predecessor waves became terminal.
This is the sole active ledger; root alone writes it.
The four assignments are CI correction, probe/operator review, terminal-ledger review, and corrected runtime review.
The runtime review stays queued until the CI correction is delivered and a worker slot is free.
The capacity remains three worker turns plus root, including any monitors.
Twelve scoped assignments across four waves do not assert500 agent launches.
Two coordinated retries in wave03 remain attempts of existing assignments.

The engine records public claims and handoffs, not authenticated process liveness.
A delivered review is not independent-account approval or native acceptance.
Root alone operates the VM; accepted6775 remains unchanged.

```sh
python3 scripts/github/waves.py summary docs/waves/2026-11-02-wave-04/ledger.json --limit 20
```

## Review checkpoint

The [terminal-ledger review](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6585816064) reconciles all30 predecessor events and both coordinated retries.
Its sole finding was a stale derived wave03 summary; root regenerated that summary in c7baad71 without changing historical events.
At revision8, the terminal-ledger and supplemental probe/operator reviews are complete; only the CI correction retains a reservation.
The corrected-runtime review remains queued behind that correction.
The probe reviewer delivered two operator findings, then root requested a correction follow-up within the same reserved assignment.
Neither the supplemental review turn nor the two earlier retries is counted as an added assignment.

The [supplemental operator review](https://github.com/alexandru-savinov/agterm-omarchy/issues/199#issuecomment-6586577428) confirms both root fixes at e3dbd076 with39 fixtures and independent reproductions.
Its original static-title limitation was corrected after tracing the host config boundary; the probe still does not measure host callback count or rate.
