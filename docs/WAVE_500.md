# A bounded queue for a 500-assignment request

[Issue #120](https://github.com/alexandru-savinov/agterm-omarchy/issues/120) requests a queue design before execution.
The current runner permits the root and **three active worker turns across the whole tree**.
A monitoring worker uses one of those three slots.
Five hundred requested assignments do not mean five hundred available tasks, identities, processes, or concurrent workers.

This guide supplements [AGENT_PROTOCOL.md](AGENT_PROTOCOL.md) and [HIVE_WAVES.md](HIVE_WAVES.md).
For this wave, all substantive worker findings, blockers, handoffs, reviews, and results belong on GitHub.
This overrides the older hive suggestion to keep worker reports private.
Local wake-ups should point to the durable GitHub message.
Do not create `Team:` counts for requested work, test fixtures, or simulated agents.

## What the tool does

`scripts/github/waves.py` is an offline, standard-library Python 3 tool for macOS and Linux.
It validates JSON and records coordination; it never executes assignment text, starts workers, calls GitHub, runs commands, or distributes credentials.
Its capacity cannot exceed three, regardless of nested delegation.
The root must also count any active work outside this ledger before allocating a slot.
Keep one canonical ledger for the entire worker pool; multiple independent ledgers cannot enforce a shared limit.

The plan schema is shown completely in [wave-500-example.json](wave-500-example.json).
Each assignment has a stable ID, concrete title, source issue, literal repository paths, dependency IDs, and an attempt limit.
Reject duplicate IDs, missing dependencies, cycles, nonliteral paths, unknown fields, and plans exceeding their requested budget.
The maximum is 500 instantiated assignments; do not fill unused capacity with placeholders or busywork.
The example contains three actual work items from #120, with dependencies, and leaves 497 requested items unscoped.
It is an executable planning example, not a ledger asserting those tasks were delegated or completed.

| Field/state | Meaning |
| --- | --- |
| `requested` | Maintainer's requested assignment budget, 1–500 |
| `assigned` | Number of concrete records, including terminal records |
| `unscoped_requested` | Requested minus concrete records; not queued work |
| `queued` | Concrete assignment awaiting a claim; dependencies may prevent eligibility |
| `claimed` | Named executor reserved a slot and paths, without reporting a start |
| `running` | Executor reported a start; not independently verified process liveness |
| `blocked` | Executor reported a blocker; its reservation remains held |
| `completed` | Running attempt submitted immutable evidence links |
| `rejected` | Explicitly abandoned or out-of-scope assignment, with a reason |

Claimed, running, and blocked reservations share the three-slot budget.
A claim requires completed dependencies and exclusive owned paths.
Path comparison is case-insensitive and checks whole path components: `scripts` overlaps `scripts/github/waves.py`, but not `scripts-other`.
Empty paths permit read-only work while still consuming a worker slot.
Paths are cooperative declarations: this tool cannot stop an editor, inspect symlinks, enforce filesystem permissions, or detect undeclared work.
Resolve aliases and external ownership in the task discussion before claiming.
One account/agent pair can hold only one reservation, even if its session changes.
Account/name/session values are self-reports, not authentication.

## GitHub coordination and publication

1. Root scopes real assignments on a bounded epic or lead thread, with explicit ownership and dependencies.
   Use existing task issues and linked PRs instead of creating 500 issues or comments at once.
2. Executors read current task discussions, post the canonical protocol claim, then root records the claim using that comment URL.
   The JSON record does not replace the public claim or grant authorization.
3. Executors post substantive changes of state and batch handoffs on the assigned issue.
   A lead may post one aggregate comment containing multiple named events and evidence links, as long as authorship and outcomes remain explicit.
   The canonical check-in header still describes exactly one named agent and issue; JSON task IDs are not parser task IDs.
4. Root applies agreed events to the single canonical ledger and publishes its revision and bounded summary beside the events, for example as a reviewed commit or existing Actions artifact.
   Preserve the event sources and exact result links.
   Do not treat an unposted local JSON update as a substantive handoff.
5. Root reconciles the latest GitHub discussion before any new assignment or retry.
   Post on changes and meaningful batch boundaries, not periodic worker heartbeats or per-record polling.
   Use the existing queue Actions summaries/artifacts and Pages for observation; those surfaces do not schedule this ledger or prove worker liveness.

This change adds no workflow, service, credential, repository setting, or dashboard automation.
Existing check-in parsing and stale rules remain intact: working claims older than four hours are stale observations, not stopped processes.
The protocol's public takeover coordination and 30-minute wait still apply when relevant.
A local lease never bypasses that rule or proves the previous worker stopped.
The older hive post budgets are planning guidelines, not GitHub rate-limit guarantees.

Use one lead-owned checkout and local filesystem for ledger writes.
The tool uses a file lock, atomic replacement, and an expected revision to reject competing/stale local writers.
Those mechanisms do not coordinate separate clones, remote machines, distributed filesystems, or manual Git merges.
A new lead first stops the former writer through public coordination and reconciles the published revision.
GitHub comments and the reviewed snapshot remain the durable record.
Do not place secrets in events, titles, or reason text: the ledger is intended to be published.

## Commands and event format

Run the small actual-task planning example; this does not create files or launch anything:

```sh
python3 scripts/github/waves.py dry-run docs/wave-500-example.json
python3 scripts/github/test_waves.py
```

The dry run reports three planned sequential batches, `requested: 500`, `assigned: 3`, `queued: 3`, and `unscoped_requested: 497`.
All execution states remain zero, and `execution_performed` is false.
The synthetic 500-record unit test separately checks 167 planned batches with at most three records per batch.
Neither result demonstrates 500 workers or 500 completed tasks.

After adapting and publicly agreeing a plan, initialize a ledger in the lead's dedicated work directory:

```sh
python3 scripts/github/waves.py init agreed-plan.json wave-ledger.json
python3 scripts/github/waves.py summary wave-ledger.json --limit 20
python3 scripts/github/waves.py apply wave-ledger.json claim.json --expected-revision 0
```

Every event has `event_id`, `operation`, `task`, `actor`, and `source`.
For example, a claim uses this shape; substitute the real actor/session and the actual public claim comment before applying:

```json
{
  "event_id": "ledger-engine-claim-1",
  "operation": "claim",
  "task": "ledger-engine",
  "actor": {"account": "authorized-account", "agent": "queue-worker", "session": "abcd1234"},
  "source": "https://github.com/alexandru-savinov/agterm-omarchy/issues/120#issuecomment-6561677455",
  "lease_seconds": 3600
}
```

The source above is the design claim, not authorization for the placeholder actor.
Do not publish the sample as a real actor check-in.
`apply` returns the assignment including its new lease token and updated revision.
The token is a fencing identifier, not a credential; copy it into the subsequent attempt events.
Each distinct event needs a stable unique ID.

| Operation | Additional fields | Requirement/result |
| --- | --- | --- |
| `claim` | `lease_seconds` (60–14400) | Eligible queued task; reserve slot/paths and increment attempt |
| `start` | `token` | Matching unexpired claimed attempt becomes running |
| `renew` | `token`, `lease_seconds` | Matching unexpired reservation extends its lease; state stays unchanged |
| `block` | `token`, `reason` | Matching reservation, even expired, becomes blocked without releasing ownership |
| `complete` | `token`, `evidence` | Matching unexpired running attempt; record evidence and release reservation |
| `retry` | `confirmed_stopped`, `reason` | Publicly coordinated stop if reserved; clear lease, return to queued within attempt budget |
| `reject` | `confirmed_stopped`, `reason` | Publicly coordinated stop if reserved; terminal rejection |

`start`, `renew`, `block`, and `complete` must match the owning account/agent/session and token.
Retry/reject are lead coordination records; the tool validates their shape, not the actor's permissions or the truth of the stop acknowledgement.
Never set `confirmed_stopped: true` just because a lease expired or a claim became stale.
Blocked reservations resume through a coordinated stopped-worker retry; there is no automatic unblock or takeover.
Expired reservations cannot renew/start/complete and continue holding capacity and paths until coordinated retry/rejection.
Rejected dependencies remain unsatisfied; explicitly reject their dependents or scope a separately reviewed follow-up plan.
Terminal assignments cannot be reopened; completion does not mean reviewer acceptance or issue closure.

An identical event replay is a no-op, even with an old expected revision.
Reusing its ID with different content is rejected.
A repeated completion with a fresh event ID may add an observation but cannot change the accepted owner, token, evidence, or completed-task count.
Old attempt tokens cannot complete a replacement attempt.
Evidence requires one to twenty exact same-repository commit, issue-comment, pull-request-review, or Actions run/job/artifact URLs.
URL validation proves shape only; root must verify the linked evidence and exact source/artifact before accepting work.
The tool does not certify hosted CI, native acceptance, or independent review.

Inputs are limited to 10 MiB and 10,000 events per wave; summary details are limited to 50 records.
Archive a full wave through public coordination and scope a reviewed successor rather than silently discarding history or copying active leases.
If a local write fails, reload the ledger and replay the same event ID after reconciling its public source.

## Joining with additional authorized executors

An additional executor needs an authorized account or explicitly shared execution account, an available real worker slot, and a distinct agent name/session.
Read `AGENTS.md`, the protocol, this guide, the current epic, and the assigned task's latest comments.
Claim one agreed scope on GitHub, work in an isolated branch/worktree, and link commits, checks, and a draft PR.
Root records the reservation only after checking existing local workers, including monitors and nested children.
A new executor replaces an available slot; it does not increase this runner's three-worker limit.
Requests for more simultaneous compute require a separately authorized execution system and coordination design.
Do not raise the constant, fabricate `Team:` counts, or spawn nested agents to evade the limit.
