# Separate review wave: 2026-11-02

[Root's handoff](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6580522377) opened this successor only after wave01 reached revision9, released all reservations, and its three worker turns ended.
This is now the sole active worker ledger; the completed [wave01](../2026-11-02-wave-01/) remains immutable history.
Root records each transition through the unchanged `scripts/github/waves.py` engine in one checkout.
Public GitHub assignments preceded activation; claim/start events reference the reviewers' actual working claims.

| Assignment | Reviewer | Public claim |
| --- | --- | --- |
| Runtime storage and shutdown | `runtime_review` | [#192](https://github.com/alexandru-savinov/agterm-omarchy/issues/192#issuecomment-6580549032) |
| Notice provenance and staging | `notice_review` | [#17](https://github.com/alexandru-savinov/agterm-omarchy/issues/17#issuecomment-6580553350) |
| Implementation-wave record | `ledger_review` | [#193](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6580636163) |

All three own no repository paths and perform read-only reviews, with disposable fixtures where relevant.
The initial ledger is revision6: three reported running assignments and no completions.
Its requested budget is three real reviews, not another request for500 workers.
The two resumed waves contain six concrete assignments; that count is not an inventory of all historical agents and excludes #127 research results.
Root is additional to the three worker slots and retains integration, candidate selection and all VM operations.

`plan.json`, `ledger.json`, `events/` and `summary.json` use the same semantics as wave01.
Recorded timestamps are observation times, not independent process-start measurements.
Fencing tokens are public coordination identifiers, not credentials.
Expired leases do not prove a worker stopped or authorize a takeover.
Completion means the assigned review was delivered, not that it approved the code or passed native acceptance.
All reviews use the same account; separate-agent work is not independent-account approval.

Inspect current recorded state with:

```sh
python3 scripts/github/waves.py summary docs/waves/2026-11-02-wave-02/ledger.json --limit 20
```
