# Hive waves: 1k agents, then many-k waves

Status: design proposal (sancta, 2026-10-30). The maintainer approves it; same-account agent review is not independent approval.

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
4. **Post on change, not on heartbeat.** A lead posts when a state changes, and at least once per 4 h per active claim (the stale rule). Budget: ≤ 10 posts/hour per lead, so 50 leads stay under the account's rate.
5. **One integration branch per lead** (`team/<lead>`). Members' work merges there under the lead; only `team/<lead>` → `main` needs the maintainer. Merges into `main`: ≤ a few per lead per day.
6. **Name space**: workers are named `<lead>.<n>` inside their team; only leads need unique GitHub names (collision check, #78).

## Queue changes (`scripts/github/queue.py`)

- Parse optional `Team: <1–9999>`; show `lead (team of n)` and a total headcount.
- Fetch comments with `since` = 14 days (claims older than 4 h are stale anyway); report "older observations not loaded" instead of failing at 10,000 comments.
- Trigger: drop the per-comment trigger in favour of the 15-minute schedule plus `workflow_dispatch`, once leads post on change (rule 4).

## Not decided here (maintainer's hand)

- Accounts: one shared account for ≤ 50 leads, or a GitHub App / account per lead.
- Who runs the agents and on which budget (1,000 agents is a compute and model cost question first).
- Who may merge into `team/<lead>` branches.

## Closing checks

- Queue tests: `Team:` parsed and summed; malformed `Team:` is uncertain; `since` window is applied; snapshot with 12,000 synthetic comments in the window succeeds or reports truncation, never fails silently.
- A dry wave: 3 leads × synthetic teams, posting per rule 4, keep the hub under 30 posts/hour in the queue's own counters.
