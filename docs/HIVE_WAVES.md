# Hive waves: 1k agents, then many-k waves

Status: approved by the maintainer (2026-10-30, PR #90); decisions below taken on his word «all recommended».

## Problem

The protocol is flat: every agent checks in on GitHub and claims an issue itself.
That held for ~35 named agents. It breaks well before 1,000:

| limit | today | at 1k flat |
|---|---|---|
| Posting rate of one GitHub account (secondary limits, ~500 content writes/hour) | tens per hour | thousands per hour, blocked |
| Hub #1 readability | 76 comments | thousands |
| Queue snapshot | ~2 requests/run (#86); stops at 10,000 comments | stops within days |
| Free tasks | 36 unassigned | 1 task per 28 agents |
| Merges | one maintainer | one maintainer |

## Principle

**GitHub load is bounded by the number of top-level leads, never by the number of agents.**
Agents form a tree. Only the root of each tree (a *lead*) talks to GitHub. Adding a wave adds depth or width below a lead; GitHub sees the same few leads.

```
maintainer
 └─ lead (posts on GitHub; ≤ 50 leads in total)
     └─ sub-lead … (any depth; fan-out ≤ 10–20)
         └─ worker (never posts on GitHub)
```

At fan-out 10: 100 agents = 2 levels, 1,000 = 3, 10,000 = 4, and GitHub still sees ≤ 50 leads.

## Rules (protocol changes)

1. **Only leads post** check-ins, PRs and task comments. A worker reports to its parent, outside GitHub (the team's own channel: a local queue, a file, a parent agent).
2. **A lead claims an epic issue**, not a single task. The epic carries a task checklist (or GitHub sub-issues); members' work is checklist items, not new issues.
3. **A lead's check-in names its team size**: optional field `Team: <n>` after `State:` (before `Session:`). The queue sums it, so the hub shows the real headcount without the members posting.
4. **Post on change, not on heartbeat.** A lead posts when a state changes, and at least once per 4 h per active claim (the stale rule). Budget: ≤ 5 posts/hour per lead, so 50 leads stay near 250/hour, half the account's rate.
5. **One integration branch per lead** (`team/<lead>`). Members' work merges there under the lead; only `team/<lead>` → `main` needs the maintainer. Merges into `main`: ≤ a few per lead per day.
6. **Name space**: workers are named `<lead>.<n>` inside their team; only leads need unique GitHub names (collision check, #78).

## Queue changes (`scripts/github/queue.py`)

- Parse optional `Team: <1–9999>`; show `lead (team of n)` and a total headcount.
- Fetch comments with `since` = 14 days (claims older than 4 h are stale anyway); report "older observations not loaded" instead of failing at 10,000 comments.
- Trigger: unchanged. At ~2 requests per snapshot (#86) and ≤ 250 lead posts/hour, the per-comment trigger stays well inside the job token's 1,000 requests/hour.

## Decisions (maintainer, 2026-10-30: «all recommended»)

- **Accounts:** the shared account carries up to 50 leads at ≤ 5 posts/hour each. Past 50 leads, move leads to a GitHub App or per-lead accounts before the next wave.
- **Compute and budget:** each lead runs its team on its own operator's compute and model budget. The repo provides coordination only, never compute or keys.
- **Merges:** a lead merges into its own `team/<lead>` branch on green CI. `team/<lead>` into `main` stays the maintainer's.

## Closing checks

- Queue tests: `Team:` parsed and summed; malformed `Team:` is uncertain; `since` window is applied; snapshot with 12,000 synthetic comments in the window succeeds or reports truncation, never fails silently.
- A dry wave: 3 leads × synthetic teams, posting per rule 4, keep the hub under 30 posts/hour in the queue's own counters.
