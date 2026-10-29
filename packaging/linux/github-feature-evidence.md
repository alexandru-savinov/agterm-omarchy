# GitHub feature evidence

Target: [`alexandru-savinov/agterm-omarchy`](https://github.com/alexandru-savinov/agterm-omarchy).
Inventory started 2026-10-29; initial read-only audit at 19:25 UTC.
Live repository evidence below was verified later on 2026-10-29 through authenticated REST and GraphQL read-back.
This is an execution checklist, not a claim that the features below have been exercised.

The requested standard is at least one real, verified use of each feature.
A committed workflow, enabled setting, documentation page, or proposed demonstration does not prove execution.
Each **proven** row needs a durable repository/run/object URL and the observable result, with a commit SHA or object ID where available.
Use **pending** for unexecuted work or an unresolved access/entitlement gate, and **unavailable** only when an identified prerequisite excludes this repository.
Unavailable rows do not count as completed demonstrations.

This inventory separates distinct capabilities from their equivalent UI, CLI, REST, and GraphQL entry points.
The user clarified the scope as every feature available to this account, including relevant account-level products.
This is the current actionable inventory, not a complete enumeration of every marketplace integration or UI action.
Account/organization/enterprise products are included below with their access gates.
Newly discovered available capabilities must be added; the current table must not silently redefine the requested scope.

## Initial access evidence

- `gh auth status` succeeded for the owner with scopes `gist`, `read:org`, `repo`, and `workflow`.
  No token value is stored here.
- At the initial audit, `GET /repos/alexandru-savinov/agterm-omarchy` returned HTTP 404.
  This was superseded by the successful live repository audit below; it is retained only as the initial observation.
- The current CLI authorization lacks `project`, `codespace`, and package-specific scopes.
  Projects requires the `project` scope in the [official CLI documentation](https://cli.github.com/manual/gh_project).
  Browser sessions or workflow tokens may offer different permissions; verify the actual route before declaring a product unavailable.
- A single authenticated owner cannot supply an independent approval of their own pull request.
  Use an actual authorized reviewer for approval and review-request demonstrations.

## Verified live snapshot

- [Repository API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy): public, `fork=true`, parent `melonamin/agterm-linux`, source `umputun/agterm`, default branch `main`.
  [Inherited main baseline](https://github.com/alexandru-savinov/agterm-omarchy/commit/74df4076b6ac18db9979ad130dc8b93f7184f3f7) remains `74df4076b6ac18db9979ad130dc8b93f7184f3f7`.
  [Omarchy implementation/CI commit](https://github.com/alexandru-savinov/agterm-omarchy/commit/c920fb1603110a65d56cb5c28c06b2219382f737) is published on `omarchy`; [comparison](https://github.com/alexandru-savinov/agterm-omarchy/compare/main...omarchy) reports two commits ahead in this snapshot.
- [License API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/license) detects MIT, with blob SHA `8291242d1c25bc374a75185d53f560b1229bdfb3`.
- [Named agent check-in](https://github.com/alexandru-savinov/agterm-omarchy/issues/1#issuecomment-6506368231) and [verified planning report](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506411483) demonstrate actual GitHub task coordination.
- [Task #4](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) has owner assignee, `area:testing` and `area:orchestration` labels, and [milestone #1](https://github.com/alexandru-savinov/agterm-omarchy/milestone/1).
  Its [parent API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/4/parent) returns hub #1; hub summary reports one child and zero completed.
- Task #4's [blocked-by API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/4/dependencies/blocked_by) returns integration task #5; [reverse relationship](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/5/dependencies/blocking) returns #4.
- [Compatibility discussion #7](https://github.com/alexandru-savinov/agterm-omarchy/discussions/7) was created through GraphQL and independently queried in category `General`.
- Issue search `repo:alexandru-savinov/agterm-omarchy is:issue label:area:testing is:open` returned task #4.
- `gh project list --owner alexandru-savinov --format json` fails with `missing required scopes [read:project]`; project mutation requires `project`.
  This is an authorization gate, not a claim that Projects is unavailable to the account.
- `GET /user` returned account type `User` and no plan information; `GET /user/orgs` returned zero accessible organizations.
  This does not prove the account has no paid personal entitlements or hidden organization membership.
- [Draft PR #6](https://github.com/alexandru-savinov/agterm-omarchy/pull/6) targets `main` from `omarchy` at `c920fb1603110a65d56cb5c28c06b2219382f737`.
  [CI run 40940912628](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40940912628) has real check results: Detect changes succeeded; Installer/scripts, SwiftLint, and Cookbook checks failed; native core tests were still running when observed.
  These demonstrate checks execution, not passing port acceptance. The published commit is unsigned (`verification.reason=unsigned`).
- Issues, Discussions, Wiki, and Projects are enabled in repository settings.
  Wiki/Projects execution is still pending; an enabled setting is not feature-use evidence.

## Review and account-access follow-up

[Review/access audit](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506602668)
records the following observations without changing OAuth grants or starting a cloud session:

- [PR #8 COMMENT review](https://github.com/alexandru-savinov/agterm-omarchy/pull/8#pullrequestreview-5703912619)
  identifies two reproduced agent-identity/parser defects at `797664db60a24bc53bd399f3d57f80c53abc76e1`.
  This is a genuine named same-account review; it is not an independent approval.
  [Re-review at f83e679d](https://github.com/alexandru-savinov/agterm-omarchy/pull/8#pullrequestreview-5703952459) verified both fixes with five passing exact-head tests; both threads were resolved and read back as resolved.
- Pagination tests against that exact source passed two-page aggregation and its fail-closed page cap.
  Workflow token permissions are read-only; comments are not evaluated as code.
- `GET /user/codespaces` returned HTTP 403, with CLI diagnosis that the token needs `codespace` scope.
  This proves an authorization gate, not missing Codespaces entitlement or quota.
- `gh agent-task list --limit 1` succeeded and returned a completed historical hosted-agent task.
  Unrelated task details were not published. This proves session-history read access only.
  Current paid entitlement, remaining credits, and new-session capability remain unverified.
  [Official cloud-agent CLI documentation](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-from-cli)
  describes session listing separately from starting work and states the paid-plan requirement.

## Artifact and code-scanning verification

[Exact verification report](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506661900)
records independently downloaded artifact `agent-queue-40942209489-1` from
[run 40942209489](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942209489).
Its `queue.json` SHA256 is `10089afcd32308c466f1d017c2e55513ac1ff47fa28361859736cb1d17ebfb22`.
It reports `pull_request` at synthetic merge SHA `5b2c9e84fc2b88c2e1664d906d7b136a74b046a6`, five tasks,
and three named agents (`portable_install`, `root`, `test_plan`).
A fourth agent's informal header did not match the documented format; this is not a claim about process liveness.

Code-scanning API analysis IDs `2057107991`, `2057106989`, and `2057105249` all reference
`f83e679d4af14cc973379db569e7176c884b3e47`, with empty errors and zero results.
The workflow checks identify Actions, JavaScript/TypeScript, and Python analyses.
This proves CodeQL analysis/upload for those languages, not Swift analysis or vulnerability remediation.

## Completed probe and delivery follow-up

[Task #12 completion evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/12#issuecomment-6507265292)
records actual execution after reviewed PR #14 merged to `81404c5f7987df4cef0dd08621789ef85532a2c1`.
[Manual run 40946525379](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379)
passed both queue/probe tests and the reusable verification job.
The dedicated inert secret was configured at 20:09:38 UTC, compared without printing its value, then deleted after artifact verification.
A subsequent repository Actions-secret listing returned `[]`.
The public variables retain channel `github-issues` and expected SHA256
`1eab8205b7741dc3cfe02411d84921b5909fcef6eb09d5775ecc55f0716dbe27`.
A future dispatch requires a new fixture and matching hash.

[Artifact 12929821616](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379/artifacts/12929821616)
has GitHub archive digest `sha256:a9f63ad2e46afc0f0b06a148d09282426ed13ed83afe647eb988a445ad7f8620`.
Downloaded `result.json` SHA256 is `89a3f00cfd9c15b310751782ebe9637c60341506a3d33a22e2e55ac29fd83c60`.
Its `passed`, `secret_delivery_verified`, source SHA, repository, run ID, channel, and expected hash all match the independently recorded configuration.
Downloaded logs contain the fixed PASS message and no raw fixture matching its complete format.

Task #12 is completed and its body contains the verified checklist.
Milestone #1 reports one open and one closed issue; hub #1 has two native children and one completed child.
The owner acknowledged the actual root handoff with reaction ID `455981681`.
These are same-account coordination records, not independent approval or third-party engagement.

[Pages evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506894638)
includes a successful push run, deployment `7422668836`, downloaded preview artifact, and byte-for-byte public HTTPS checks.
The deployment status was independently read back as `success` in environment `github-pages`.
[Ruleset enforcement evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506754179)
records a rejected harmless validation-branch rewind; the active rule configuration was independently queried.

## Repository and collaboration

The [repository documentation](https://docs.github.com/en/repositories) and
[pull-request documentation](https://docs.github.com/en/pull-requests) define these capability groups.
The proposed uses below are this project's acceptance scenarios.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| R01 | Public repository | Create public repository, push source, independently fetch its public URL and commit. | proven — [public repository](https://github.com/alexandru-savinov/agterm-omarchy), [published Omarchy commit](https://github.com/alexandru-savinov/agterm-omarchy/commit/c920fb1603110a65d56cb5c28c06b2219382f737), and independent REST read-back |
| R02 | Fork network | Create as a real fork where appropriate and verify `fork` plus `parent`/`source`; otherwise demonstrate a separate authorized fork. A copied Git history is not a GitHub fork. | proven — [repository API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy) confirms fork, parent, and source |
| R03 | Branches and compare | Push a task branch and inspect GitHub's compare diff against the base SHA. | proven — [main...omarchy comparison](https://github.com/alexandru-savinov/agterm-omarchy/compare/main...omarchy) returned actual changed files and head `c920fb16` |
| R04 | Git commits/history/blame | Publish an attributable change, inspect commit diff and line history. | proven — [probe commit/diff](https://github.com/alexandru-savinov/agterm-omarchy/commit/81404c5f7987df4cef0dd08621789ef85532a2c1) and GraphQL blame of `scripts/github/channel_probe.py` attribute lines 1–51 to that merged commit |
| R05 | Signed commits or tags | Verify GitHub reports the published object as verified, recording signer and SHA. Do not impersonate another signer. | proven — [squash merge df1c77bd](https://github.com/alexandru-savinov/agterm-omarchy/commit/df1c77bdacb3a225fd15e64061021a265b32375c) has GitHub committer/signature, `verified=true`, `reason=valid`; author identity remains owner |
| R06 | Repository metadata/topics | Set relevant description/topics/homepage and read them back through the repository API. | proven — [repository API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy) read-back verifies Omarchy/ARM64/GTK4/Swift/terminal topics, WIP description, and live Pages homepage URL |
| R07 | License detection | Preserve upstream license and verify GitHub's license API recognizes the repository. | proven — [license API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/license) recognizes MIT and blob SHA above |
| R08 | README and rendered Markdown | Publish installation/readiness documentation and verify rendered links, tables, and code examples. | pending — rendered README API verified live hub link, hyperlinks, and code blocks; no table present in the inspected README |
| R09 | Code search/navigation | Execute a repository-scoped search returning a concrete source symbol and inspect its permalink. Indexing can lag. | pending — new probe symbol search returned zero indexed matches; direct source and blame access succeed |
| R10 | Repository template | Use a narrowly scoped development-template repository to generate a genuine downstream repository, if wanted; record parent template metadata. Do not relabel the main fork simply for this check. | pending — separate artifact decision |
| R11 | Git LFS | Push and retrieve a legitimate large test artifact through LFS; verify content hash and LFS object transfer. Ordinary Git blobs are not evidence. | pending — real artifact and quota needed |
| R12 | Commit status / Checks API | Produce a real status or check for a published commit; verify its conclusion and details URL. | proven — [real CI check results](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40940912628) include success and failure conclusions for the published head |
| R13 | Repository notifications | Subscribe to a relevant issue/repository and verify subscription state; delivery requires actual notification evidence. | pending |
| R14 | Stars/watchers/social metadata | Verify a deliberate owner action and read-back if desired; never manufacture third-party adoption. | pending |
| R15 | Wiki | Publish a useful troubleshooting page to the wiki repository and verify its rendered page and Git history. | proven — [Home](https://github.com/alexandru-savinov/agterm-omarchy/wiki/Home), [Troubleshooting](https://github.com/alexandru-savinov/agterm-omarchy/wiki/Troubleshooting), and [Joining](https://github.com/alexandru-savinov/agterm-omarchy/wiki/Joining) all return public HTTP 200 with rendered content; Wiki Git HEAD 18c3e0ba86ee5966bc08fa410ae256e1d08a437d read back independently |
| R16 | Repository insights | Inspect contributors, commits, pulse, and dependency/network information after activity exists. Record screenshots or exported responses with timestamp. | pending |
| R17 | Traffic insights | Read owner-only views/clones/referrers statistics after GitHub has data; preserve privacy and timestamps. Empty data proves access only. | pending — owner views/clones endpoints return HTTP 200 with 14 daily buckets but all counts zero; access proven, populated metrics unobserved |
| R18 | Community health | Publish contribution guidance, code of conduct, security policy, support and templates; verify GitHub's community-profile response. | pending — community-profile endpoint returned HTTP 404 for this fork; community files and rendered entry points still require validation |
| R19 | Citation metadata | Add accurate software citation metadata only if appropriate, then verify GitHub's citation rendering. | pending |
| R20 | Funding / Sponsors | Configure an actual funding destination only when supplied by the owner; verify destination and rendered funding control. Never invent beneficiaries. | pending — owner destination/account needed |

## Issues, Projects, and Discussions

[Issues](https://docs.github.com/en/issues) supports project work tracking.
[Sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
and [issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)
are separate relationships; a Markdown task list does not demonstrate either.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| I01 | Issue lifecycle | Track a real port defect/task, update its body, complete it, and verify final state and closing reason. | proven — [task #12](https://github.com/alexandru-savinov/agterm-omarchy/issues/12) body updated with verified checklist and evidence, then read back CLOSED / COMPLETED |
| I02 | Issue forms/templates | Submit a real test report through the configured form; preserve the issue URL and resulting fields. A YAML file alone is not execution. | pending |
| I03 | Labels | Create useful architecture/testing labels, apply them to work, verify issue label membership. | proven — [task #4](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) uses both newly created area labels; API membership verified |
| I04 | Assignees | Assign a real responsible contributor and verify assignment. Do not assign unrelated people. | proven — [assigned task #4](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) has the actual owner identity; named agent claim linked above |
| I05 | Milestones | Group release criteria in a milestone, complete one item, verify progress counts. | proven — [milestone #1](https://github.com/alexandru-savinov/agterm-omarchy/milestone/1) read-back after completed #12 reports `open_issues=1`, `closed_issues=1` |
| I06 | Comments/reactions/task lists | Record a useful test observation and update a completion checklist; verify timeline and reaction/task state. | proven — [completion checklist and evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/12#issuecomment-6507265292), completed body checklist, and owner +1 acknowledgement of [root handoff](https://github.com/alexandru-savinov/agterm-omarchy/issues/12#issuecomment-6507161832), reaction ID 455981681; no third-party engagement claimed |
| I07 | Sub-issues | Add a real test/build child issue to the port tracking issue; query relationship and progress. | proven — [hub #1](https://github.com/alexandru-savinov/agterm-omarchy/issues/1) native children #4 open and #12 completed; read-back summary `total=2`, `completed=1`, `percent_completed=50` |
| I08 | Issue dependencies | Mark packaging blocked by verified build/testing work; query blocking and blocked-by edges, then resolve the actual prerequisite. | pending — [actual blocking edge](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/4/dependencies/blocked_by) and reverse read-back proven; prerequisite resolution awaits VM integration |
| I09 | Issue types | Assign a supported issue type and query it. Organization-managed types may require an organization-owned repository. | pending — ownership/API capability check |
| I10 | Linked branches and PR closing references | Link a task branch and merge a fixing PR that closes the issue; inspect timeline cross-references. | pending |
| I11 | Issue search/filtering | Retrieve an issue with label, milestone, state, and relationship filters; record query and result URL. | pending — combined repository/closed-state/label/milestone query returned #12; relationship-filter search returned zero, native parent/child APIs verified separately |
| I12 | Issue artifacts | Attach genuine test/research material using the actual issue-artifact feature where exposed; record its object/link. A normal comment attachment is not equivalent. | pending — capability/API check |
| I13 | Projects creation and repository link | Create a public project for port delivery, link repository, and retrieve project URL and ID. | pending — live CLI rejected missing `read:project`; mutations need `project` authorization |
| I14 | Project fields/items | Add real issues/PRs and a draft planning item; set status, date, and iteration/custom fields; read back values. | pending — Projects access |
| I15 | Project board/table/roadmap views | Save and inspect each view using real project data; record view URLs and screenshots. | pending — Projects access and UI |
| I16 | Project workflows/automation | Trigger a real item-added or issue-closed automation and verify resulting field/status change. | pending — Projects access |
| I17 | Project charts/insights | Create a useful chart from actual task state and inspect rendered result. | pending — Projects access and data |
| I18 | Discussions/categories | Enable Discussions and start a substantive port/testing discussion in a suitable category. | proven — [General discussion #7](https://github.com/alexandru-savinov/agterm-omarchy/discussions/7) created and queried through GraphQL |
| I19 | Discussion answers and polls | Resolve a genuine Q&A and record accepted answer; use a real decision poll only with meaningful choices. | pending — participants/use case |
| I20 | Discussion-to-issue conversion | Convert an actionable discussion into a linked task and inspect cross-references. | pending — real actionable discussion |

## Pull requests and repository governance

Public-repository [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
can enforce branch and tag constraints.
[Merge queues](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
require organization ownership for public repositories; this planned target belongs to a personal account.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| P01 | Pull request | Open the actual port change, verify base/head SHAs, diff, checks, and description. | proven — [draft PR #6](https://github.com/alexandru-savinov/agterm-omarchy/pull/6) has verified base/head, actual diff, and real checks; failures remain open |
| P02 | Draft/ready transition | Start unfinished work as draft, complete readiness checks, transition to ready, and verify timeline. | proven — [PR #14](https://github.com/alexandru-savinov/agterm-omarchy/pull/14) created draft, checks passed, timeline `ready_for_review` at 20:08:35 UTC and merged at 20:08:42 UTC |
| P03 | PR template | Open a PR with the project template populated with actual validation and risks. | pending |
| P04 | Inline review/comment/suggestion | Review a real line, propose a useful change, apply it, and resolve the conversation with evidence. | proven — [inline findings](https://github.com/alexandru-savinov/agterm-omarchy/pull/8#pullrequestreview-5703912619), actual fixes, [five-test re-review](https://github.com/alexandru-savinov/agterm-omarchy/pull/8#pullrequestreview-5703952459), and both resolved threads; same-account identity explicit |
| P05 | Requested review and independent approval | Request an authorized reviewer; obtain genuine review with reviewer identity, state, and commit SHA. | pending — separate reviewer needed |
| P06 | Requested changes and re-review | Record a genuine review defect, fix it, and obtain an updated review. Do not manufacture criticism to simulate collaboration. | pending — [real defects and re-review](https://github.com/alexandru-savinov/agterm-omarchy/pull/8#pullrequestreview-5703952459) completed with COMMENT reviews; formal REQUEST_CHANGES under an independent identity is unproven |
| P07 | CODEOWNERS | Add valid ownership rules, create a matching change, and verify review routing and rule interpretation. | pending — independent eligible owner needed for request |
| P08 | PR merge | Merge tested work and verify merge commit plus linked issue closure. | pending — tested [PR #14](https://github.com/alexandru-savinov/agterm-omarchy/pull/14) merged to 81404c5f; linked #12 closed after runtime verification, not automatically by a closing keyword |
| P09 | Squash/rebase/merge strategies | Exercise each strategy on a useful, separate PR and verify resulting commit graph. One merge does not prove all strategies. | pending — PR #8 squash result and PR #10 two-parent merge commit verified; PR #11 single-parent rewritten commit is consistent with rebase but requires actual merge-method action record to distinguish custom squash |
| P10 | Merge conflict resolution | If a real conflict occurs, resolve it and preserve conflicting base/head and resolution evidence; a documented fixture may demonstrate mechanics without harming actual work. | pending |
| P11 | Auto-merge | Enable on a PR waiting for a real required check and verify automatic merge after success. | pending |
| P12 | Branch protection | Protect a dedicated validation branch, attempt a harmless disallowed update, record denial, and complete an allowed PR path. | pending |
| P13 | Branch rulesets | Apply relevant checks/force-push restrictions and demonstrate enforcement, not just creation. | proven — [rule suite 4801459871](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/rulesets/rule-suites/4801459871) records failed force-push/PR-only enforcement on validation/governance under ruleset 26157559; later main suites 4801569141, 4801639053, and 4801860351 passed |
| P14 | Tag rulesets | Protect a release/test tag pattern and demonstrate denied unauthorized modification of a dedicated validation tag. | pending |
| P15 | Required status checks | Show a required test failure blocks a dedicated PR and a corrected passing check permits merge. | pending |
| P16 | Required conversation resolution | Show an unresolved genuine review thread gates a PR, then resolve it and verify eligibility. | pending — reviewer/thread |
| P17 | Merge queue | Enqueue and merge a checked PR through a queue, retaining merge-group run evidence. | unavailable — personal-owned target; organization repository required |
| P18 | Push rulesets | Demonstrate supported path/size restrictions in an eligible repository/fork network. | unavailable for this target under documented personal public-repository scope |
| P19 | Branch cleanup and recovery | Delete only a merged task branch, restore it if testing recovery, and verify references. | pending |

## Actions, deployments, and distribution

[Actions](https://docs.github.com/en/actions), [Packages](https://docs.github.com/en/packages),
and [Pages](https://docs.github.com/en/pages) are separate products.
An environment job produces deployment objects and statuses; these can be checked independently of the workflow run.
Public repositories support [environments and deployment protection](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| A01 | Push and PR workflows | Trigger both with real commits/PRs, record run URLs, events, SHAs, and conclusions. | proven — [Pages push run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40944102924) succeeded at 0eacad7f; [PR queue run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942209489) succeeded and downloaded artifact verified at its synthetic merge SHA |
| A02 | Manual workflow dispatch | Dispatch a useful packaging/smoke workflow with inputs and verify recorded input/effect. | proven — [manual probe run 40946525379](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379) at 81404c5f passed tests and comparison; supplied repository configuration recorded in downloaded JSON |
| A03 | Scheduled workflow | Observe an actual scheduled run; a manual dispatch of the same workflow does not prove the schedule. | pending — scheduled event must occur |
| A04 | Matrix jobs | Run native supported architecture/toolchain jobs and verify every matrix leg; do not label cross-compilation as runtime testing. | proven — [run 40941708442](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40941708442) passed all nine jobs on native Ubuntu x86_64/ARM64 runners; each GTK leg passed 553 tests/81 suites and 49 AT-SPI scenarios plus debug/Release builds and staging; tested synthetic merge 781b194c, not later native fixes |
| A05 | Job dependencies/outputs | Feed a verified build output/version into dependent package and validation jobs. | pending |
| A06 | Cache save and restore | Capture a save in one run and an actual cache hit in a later run with the same key. | pending — [both native cache saves](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6507407346) succeeded after initial misses; later successful restore still unobserved |
| A07 | Workflow artifacts | Upload real test/build evidence, download it independently, and verify contents/hashes. | proven — [run artifact](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942209489) downloaded independently; queue contents and SHA256 recorded above |
| A08 | Job summaries/annotations | Publish meaningful test summary and actionable compiler/test annotations; verify run display. | pending — reusable probe writes a successful job summary; independent rendered-summary inspection and actionable annotation verification remain outstanding |
| A09 | Reusable workflow/composite action | Invoke shared build/test logic from another workflow and verify nested run/step output. | proven — [probe / Verify probe delivery](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379) is the successful nested job from local reusable workflow, with verified result artifact |
| A10 | Variables/secrets | Read a non-sensitive variable and consume a dedicated benign test secret without printing it; verify comparison success and delete the test secret afterward. | proven — [probe creation/comparison/download/cleanup evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/12#issuecomment-6507265292); expected hash and channel match, temporary repository secret deleted and listing read back empty |
| A11 | Workflow concurrency/cancellation | Run a deliberately bounded test group twice and verify superseded run cancellation without interrupting release work. | pending |
| A12 | Failure/retry handling | Rerun a genuinely failed job after diagnosis and verify attempt number plus corrected outcome. | pending |
| A13 | Minimal token permissions | Verify a limited-permission job performs its needed action; denied unrelated action can be checked against an isolated fixture. | proven — [probe run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379) completed checkout/tests/artifact with `contents: read`; checkout credential persistence disabled; no unrelated mutation attempted |
| A14 | Hosted ARM64/x86_64 runners | Execute native architecture probe and build/test on each available runner, retaining `uname` and result. | pending — native Ubuntu x86_64/ARM64 runner labels and passing architecture-specific builds/tests verified in [run 40941708442](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40941708442); separate uname probe output was not retained in these logs |
| A15 | Self-hosted runner | Register a dedicated isolated runner, run a bounded trusted job, and remove registration after use. Do not expose the interactive Mac or personal VM to untrusted PR execution. | pending — isolated runner and policy needed |
| A16 | Environment/deployment | Run a job against a named test environment and verify deployment `success`, SHA, and environment URL. | proven — [Pages run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40944102924) created deployment 7422668836; independently read back `success`, environment `github-pages`, public environment URL |
| A17 | Deployment branch protection | Demonstrate rejection from a dedicated disallowed test branch and success from the permitted branch. | pending |
| A18 | Deployment reviewer/wait timer | Observe the protected wait, genuine approval where applicable, and subsequent execution. | pending — reviewer or timed gate |
| A19 | OIDC | Exchange a job identity for a narrowly scoped credential with a real provider; redact JWTs and record provider audit evidence. `id-token: write` alone is not use. | pending — external provider integration |
| A20 | Artifact attestations | Attest a real release artifact, download it, and verify provenance against repository/workflow/SHA. | pending |
| A21 | GitHub Pages | Deploy useful Omarchy documentation, fetch the public URL, and verify content version and deployment run. | proven — [live hub](https://alexandru-savinov.github.io/agterm-omarchy/) and [download/public byte comparison](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506894638) at merged 0eacad7f; status remains work-in-progress |
| A22 | Pages custom domain | Verify an owner-supplied domain, DNS, and HTTPS certificate; do not invent or take over a domain. | pending — owned domain/DNS access |
| A23 | Release/prerelease and notes | Publish an honestly labeled tested artifact release/prerelease with notes and tag provenance. | pending |
| A24 | Release assets/download | Upload architecture-labeled tarball/checksums, download from release URL, verify checksum and launch/test it. | pending |
| A25 | Packages/container registry | Publish a useful build/test image or package with provenance, pull by digest/version, and use it. | pending — workflow `packages: write` or appropriate auth |
| A26 | Package visibility/access | Verify intended public visibility and unauthenticated consumption of the published package. | pending |
| A27 | Comment and repository dispatch events | Trigger useful event-driven orchestration and verify event, source SHA, result, and artifact. | proven — [issue_comment run 40942841512](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942841512) and [repository_dispatch run 40942858795](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942858795) both succeeded at df1c77bd; root verified queue artifacts |

## Security and supply chain

The [GitHub security overview](https://docs.github.com/en/code-security/getting-started/github-security-features)
distinguishes free public-repository scanning from organization/paid capabilities.
[Secret scanning](https://docs.github.com/en/code-security/how-tos/secure-your-secrets/detect-secret-leaks/enable-secret-scanning)
is available on public repositories, but configuration does not prove that an alert or blocked push occurred.
Never leak a live credential, create a real exploitable release, request a fake CVE, or publish a fabricated vulnerability just to fill this table.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| S01 | Security policy/private reporting | Publish accurate reporting instructions, enable private reporting if supported, and verify the reporting entry point without filing a fake vulnerability. | pending |
| S02 | Dependency graph | Verify GitHub ingests actual package manifests/lockfiles and shows the expected dependency relationships. | pending |
| S03 | Dependency submission/SBOM | Submit build-resolved dependencies if necessary, export an SBOM, and compare dependencies to the actual build. | pending |
| S04 | Dependency review | Run review on a real dependency-changing PR and inspect its report; no artificial vulnerable production dependency. | pending |
| S05 | Dependabot version updates | Configure relevant ecosystem updates and observe a real generated update PR with passing validation. | pending |
| S06 | Dependabot alerts/security updates | Inspect actual alerts and, if one exists, validate and fix it through a real security-update PR. Zero alerts proves scanning state, not alert remediation. | pending — genuine alert may not exist |
| S07 | Code scanning/CodeQL | Run analysis for a supported language present here, verify uploaded analysis and commit SHA in GitHub. Swift toolchain/runner support must be checked. | proven — [CodeQL run](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40942197068) and three analysis API objects match head f83e679d; Actions/JS/Python only, Swift coverage unproven |
| S08 | SARIF integration | Upload valid results from a real additional scanner and verify corresponding tool/category in code scanning. Empty or fabricated alerts are not findings. | pending |
| S09 | Secret scanning | Verify enabled state and completed coverage where exposed; handle a genuine alert only if one exists. | pending |
| S10 | Secret push protection | Use only a GitHub-documented inert test fixture in an isolated throwaway branch if supported; capture the blocked push without publishing a live secret. | pending — safe fixture verification required |
| S11 | Security advisory and remediation | Create/disclose an advisory only for a verified real vulnerability; demonstrate private remediation and patched release when warranted. | pending — real vulnerability required |
| S12 | CVE request | Request a CVE only for an eligible verified vulnerability with coordinated disclosure. | pending — legitimate vulnerability/eligibility required |
| S13 | Security campaigns/organization overview | Inspect and act on real organization-wide security data with the required organization and licensed product. | pending — unavailable in this personal repository; account-wide organization/product availability unresolved |
| S14 | Custom secret patterns/delegated bypass | Demonstrate an eligible organization's configured pattern or bypass workflow using approved fixtures and participants. | pending — organization/product eligibility required |
| S15 | Autofix/security AI | Apply and validate a genuine supported scanning remediation proposal; preserve alert and patch provenance. | pending — supported finding and entitlement |

## Cloud development, AI, and integrations

[Codespaces](https://docs.github.com/en/codespaces/about-codespaces/what-are-codespaces) uses account compute and storage quotas.
Verify entitlement and budget before starting; stopping compute does not remove stored workspace usage.
[Copilot plan availability](https://github.com/features/copilot/plans) and
[third-party coding-agent documentation](https://docs.github.com/en/enterprise-cloud%40latest/copilot/concepts/agent-capabilities/about-agents/about-third-party-coding-agents)
can differ while previews change, so the account's actual capability response is required.
A local Codex session is not evidence of GitHub Copilot or a GitHub-hosted coding-agent session.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| C01 | Codespaces/dev containers | Create a workspace from committed devcontainer configuration, run a real build/check, record workspace/commit/logs, stop/delete when done. | pending — live API returns HTTP 403 and missing `codespace` scope; no workspace started, entitlement/quota unverified |
| C02 | Codespaces port forwarding | Serve local project documentation inside that Codespace and verify the intended private forwarded endpoint. | pending — running Codespace |
| C03 | Codespaces prebuilds | Generate and consume a real prebuild, verify its log and workspace provenance. | pending — applicable repository configuration and storage budget |
| C04 | Copilot coding agent | Assign a useful bounded issue; verify a genuine GitHub agent session, generated PR, tests, and human review. | pending — historical task listing succeeds; [new real task creation](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506810534) returned HTTP 403, no session or PR created; entitlement/access unresolved |
| C05 | Copilot code review | Request review of the actual change, inspect resulting findings, and validate any applied fix. | pending — entitlement/policy/credits |
| C06 | Copilot custom instructions/agents | Observe a hosted agent use committed instructions and verify a relevant result; files alone are configuration. | pending — agent session |
| C07 | Copilot Chat / CLI | Use on a real repository question/task and retain non-sensitive interaction/output evidence tied to the commit. | pending — client/account access |
| C08 | Copilot Spaces | Create a useful context space and query it against project sources; record accessible space and result. | pending — UI/account capability |
| C09 | Third-party coding agents | Invoke a supported GitHub-hosted Claude/Codex agent on real bounded work and verify its session/artifact. | pending — availability/credits/policy |
| C10 | GitHub Models | Run a bounded useful prompt/evaluation with an eligible account, retaining model/version and result without secrets. | pending — product access and budget |
| C11 | REST and GraphQL APIs | Read and mutate relevant project objects through authorized APIs, then independently verify resulting state. | proven — REST planning mutations/read-back and GraphQL discussion mutation/read-back in [evidence report](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506411483) and [discussion #7](https://github.com/alexandru-savinov/agterm-omarchy/discussions/7) |
| C12 | GitHub CLI | Perform actual repository lifecycle operations with `gh`, preserving object/run URLs and commands without credentials. | proven — `gh issue comment`, `gh label create`, `gh issue edit`, and `gh api` produced [verified issue metadata](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) |
| C13 | Webhooks | Deliver a real repository event to an owner-controlled endpoint, verify delivery ID, signature validation, and response. | pending — receiver endpoint and hook access |
| C14 | GitHub App installation | Install a narrowly scoped owned/approved app and verify an actual useful API action under its identity. | pending — app and installation authorization |
| C15 | OAuth integration | Exercise an approved OAuth integration with minimal scopes and verify its useful result; never create token grants merely as decoration. | pending — approved integration |
| C16 | Marketplace/custom Action | Consume a suitable Action in a successful run; publishing a custom Action requires a genuinely reusable implementation and separate release evidence. | proven — [successful probe jobs](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40946525379) executed `actions/checkout@v7` and `actions/upload-artifact@v6`; downloaded artifact verifies useful Action consumption, not publication of a custom Action |
| C17 | Gists | Publish a useful non-sensitive standalone snippet only if it belongs outside the repository, then verify rendered content/revision. | pending — genuine standalone artifact |
| C18 | Desktop/mobile/github.dev surfaces | Perform a useful operation in each requested client and verify the resulting repository state. Equivalent clients are not separate underlying repo features. | pending — authenticated Safari accessibility UI created the first Wiki page and Git verified its exact content; Desktop/mobile/github.dev surfaces remain unexercised |
| C19 | Organization teams/roles/audit log/SSO/SCIM | Verify actual organizational administration with appropriate owner/enterprise access. No personal-repository setting substitutes for these features. | pending — no organizations accessible through current API authorization; account-wide availability unresolved |
| C20 | Enterprise policies/runners/networking/managed users | Use an explicitly authorized eligible enterprise and retain policy/enforcement evidence. | pending — no verified enterprise entitlement or administrative access |

## Wiki and additional agent connections

[Wiki publication evidence](https://github.com/alexandru-savinov/agterm-omarchy/issues/16#issuecomment-6507593592)
records Home creation through the authenticated Safari accessibility UI after the empty Wiki Git endpoint returned 404.
Browser scripting stayed disabled; no browser security setting was changed.
The pasted Home content was compared before saving, then its cloned Git file matched byte for byte.
Troubleshooting and Joining were subsequently committed and pushed through the Wiki Git repository.
All three public pages returned HTTP 200 and contained the expected rendered Markdown.
Wiki commits are `04b19fe53cdf065bcf5a7403ba536fa20576b02a` (Home)
and `18c3e0ba86ee5966bc08fa410ae256e1d08a437d` (Troubleshooting/Joining).

Additional sessions posted real [queue-review](https://github.com/alexandru-savinov/agterm-omarchy/issues/1#issuecomment-6507432791),
[integration-review](https://github.com/alexandru-savinov/agterm-omarchy/issues/1#issuecomment-6507437284),
and [Linux-review](https://github.com/alexandru-savinov/agterm-omarchy/issues/1#issuecomment-6507469523) check-ins and claimed #19/#18/#20.
A [Claude Code check-in](https://github.com/alexandru-savinov/agterm-omarchy/issues/1#issuecomment-6507495868)
reported a separate Linux host and waited for an unowned task.
These comments prove observed GitHub coordination under the authorized shared account.
They do not establish independent authenticated reviewer identities, process liveness, or GitHub-hosted Copilot execution.

[Independent matrix and governance handoff](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6507440900)
records exact job, artifact, merge-graph, rule-suite, environment, and rendered README observations.
Both native GTK jobs completed at source head `aac39b6481e0aa974e92b13c3cd8757b34a1e428`
and tested merge `781b194cad54b4db5c05d576ee9074e2aa7f46e4`.
Those published artifacts predate the later native GLib and Hyprland fixes; they must not be represented as the final-source release.

## Current coverage and remaining gates

The 2026-10-29 follow-up records **34 proven**, **85 pending**, and **2 unavailable for this target** across 121 rows.
Counts describe this inventory only; they do not establish exhaustive account-wide coverage.
The comment/repository-dispatch capability was added as A27 after observing its useful execution.

[Native debug acceptance](https://github.com/alexandru-savinov/agterm-omarchy/issues/5#issuecomment-6507246697)
and [relocated release acceptance](https://github.com/alexandru-savinov/agterm-omarchy/issues/15#issuecomment-6507327094)
both passed all seven real Hyprland Wayland gates on ARM64 with a process-local Mesa software-rendering override.
[Native packaging/tests](https://github.com/alexandru-savinov/agterm-omarchy/issues/15#issuecomment-6507348032)
record 553 frontend tests, eight installer fixtures, isolated relocation checks, and a development archive with source `5e37cb75`.
Core acceptance separately passed 4,025 tests in 175 suites.
These are application acceptance results, not by themselves GitHub release publication or hosted architecture-matrix evidence.
Release publication, attestations, cache reuse, and hosted architecture-specific job results remain separate evidence streams.
The current account gates are Projects/Codespaces authorization and a denied hosted-agent creation attempt.
Independent approvals require a separate eligible reviewer; account-wide organization/enterprise availability remains unresolved.
Funding, custom domains, external webhook/OIDC receivers, and security advisory/CVE demonstrations need real appropriate subjects.
Nothing in those pending rows authorizes invented beneficiaries, paid entitlements, fabricated findings, or misleading approvals.

## Execution order and evidence capture

1. Publish the repository and the actual Omarchy source changes with explicit readiness status.
2. Track the real remaining build/test/package tasks as issues, relationships, labels, and milestone work.
3. Open the implementation PR, run native tests, fix real failures, and preserve test artifacts.
4. Exercise applicable branch protections and required checks on a separate validation branch before protecting the delivery branch.
5. Obtain independent review, merge passing work, and publish verified package, attestation, Pages documentation, and an honestly labeled release.
6. Request missing feature access only when needed; record concrete API errors, entitlement responses, and product restrictions.
7. Update each row with actual object/run URLs, SHA, timestamp, observable outcome, and a cleanup note.
8. Review every pending/unavailable row before any claim of “every GitHub feature used.” Do not count planned, configured, denied, or unavailable work as proven use.

Suggested evidence record:

```json
{
  "feature": "A07",
  "status": "proven",
  "url": "ACTUAL_WORKFLOW_RUN_URL",
  "commit": "ACTUAL_COMMIT_SHA",
  "observed_at": "UTC_TIMESTAMP",
  "result": "downloaded artifact and verified SHA256 against the build manifest",
  "artifact_url": "ACTUAL_ARTIFACT_URL",
  "sha256": "ACTUAL_SHA256",
  "cleanup": "artifact retained under the workflow retention policy"
}
```

The placeholder example is not evidence and must never be copied into a completed row unchanged.
For checks whose API provides no public permalink, preserve a redacted response artifact and identify the exact read command and observation time.
