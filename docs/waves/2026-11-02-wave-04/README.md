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
