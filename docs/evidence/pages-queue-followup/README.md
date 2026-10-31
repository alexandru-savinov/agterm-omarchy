# Pages queue proposal for findings in #79

Task: [#81](https://github.com/alexandru-savinov/agterm-omarchy/issues/81), part of team [#80](https://github.com/alexandru-savinov/agterm-omarchy/issues/80).
Baseline: `2dd72526bfbd88f149cf51ed13d3ba4c1d07312e` (main after PR #76).
Author: Codex agent `codex_team_pages`, shared account; this is not independent approval.

This directory contains an inert patch proposal and an executable regression suite.
It does not change the active Pages generator or workflow.
Sancta retains implementation ownership under #75; the owner can adopt, adapt or decline the proposal.

The patch makes a roster card's phase badge and filter use its latest queue claim when present, while preserving curated role, work, phase, timestamps and historical report in the machine-readable roster.
The historical state line is explicitly labeled curated.
It counts all displayed identities in both pages and uses the same phases for team summary totals.
The footer identifies account labels per card and avoids treating separate accounts as independent approval.
The existing queue behavior, including its snapshot-age warning, stays intact.
A stale snapshot still supplies its observed phases; this is an observed claim, not live status.

## Reproduce

Use Python 3.12 (standard library only) and Git.
From the checkout containing these artifacts, create a disposable checkout pinned to the baseline:

```sh
git worktree add --detach /tmp/pages-followup-replay 2dd72526bfbd88f149cf51ed13d3ba4c1d07312e
python3 docs/evidence/pages-queue-followup/test_regressions.py /tmp/pages-followup-replay
git -C /tmp/pages-followup-replay apply "$PWD/docs/evidence/pages-queue-followup/proposal.patch"
python3 docs/evidence/pages-queue-followup/test_regressions.py /tmp/pages-followup-replay
python3 /tmp/pages-followup-replay/docs/omarchy-pages/test_build.py
python3 /tmp/pages-followup-replay/scripts/github/test_queue.py
```

The first test invocation intentionally exits 1: four methods run, with five assertion failures across three methods (three phase subtests plus count and footer failures).
The no-queue fallback passes on the baseline.
The patched run exits 0 with four methods passing; all four canonical queue states are covered.
Existing Pages tests: 14 pass.
Existing queue tests at this baseline: 30 pass.
All these results were observed locally on Linux with Python 3.12.12.
The account and two-card roster are synthetic fixtures, not reports that an external contributor joined.

To discard the disposable checkout and applied proposal:

```sh
git worktree remove --force /tmp/pages-followup-replay
```

No browser interaction, hosted preview, native runtime validation, deployment, or release readiness is claimed.
Before adoption, the owner should port the regressions into the normal Pages test suite and review the chosen current-claim filtering semantics.
The standalone regression file is intentionally outside test discovery and requires a checkout argument.
