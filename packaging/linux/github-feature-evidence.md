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

## Repository and collaboration

The [repository documentation](https://docs.github.com/en/repositories) and
[pull-request documentation](https://docs.github.com/en/pull-requests) define these capability groups.
The proposed uses below are this project's acceptance scenarios.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| R01 | Public repository | Create public repository, push source, independently fetch its public URL and commit. | proven — [public repository](https://github.com/alexandru-savinov/agterm-omarchy), [published Omarchy commit](https://github.com/alexandru-savinov/agterm-omarchy/commit/c920fb1603110a65d56cb5c28c06b2219382f737), and independent REST read-back |
| R02 | Fork network | Create as a real fork where appropriate and verify `fork` plus `parent`/`source`; otherwise demonstrate a separate authorized fork. A copied Git history is not a GitHub fork. | proven — [repository API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy) confirms fork, parent, and source |
| R03 | Branches and compare | Push a task branch and inspect GitHub's compare diff against the base SHA. | proven — [main...omarchy comparison](https://github.com/alexandru-savinov/agterm-omarchy/compare/main...omarchy) returned actual changed files and head `c920fb16` |
| R04 | Git commits/history/blame | Publish an attributable change, inspect commit diff and line history. | pending |
| R05 | Signed commits or tags | Verify GitHub reports the published object as verified, recording signer and SHA. Do not impersonate another signer. | pending |
| R06 | Repository metadata/topics | Set relevant description/topics/homepage and read them back through the repository API. | pending — description and relevant topics verified; project homepage awaits deployment |
| R07 | License detection | Preserve upstream license and verify GitHub's license API recognizes the repository. | proven — [license API](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/license) recognizes MIT and blob SHA above |
| R08 | README and rendered Markdown | Publish installation/readiness documentation and verify rendered links, tables, and code examples. | pending |
| R09 | Code search/navigation | Execute a repository-scoped search returning a concrete source symbol and inspect its permalink. Indexing can lag. | pending |
| R10 | Repository template | Use a narrowly scoped development-template repository to generate a genuine downstream repository, if wanted; record parent template metadata. Do not relabel the main fork simply for this check. | pending — separate artifact decision |
| R11 | Git LFS | Push and retrieve a legitimate large test artifact through LFS; verify content hash and LFS object transfer. Ordinary Git blobs are not evidence. | pending — real artifact and quota needed |
| R12 | Commit status / Checks API | Produce a real status or check for a published commit; verify its conclusion and details URL. | proven — [real CI check results](https://github.com/alexandru-savinov/agterm-omarchy/actions/runs/40940912628) include success and failure conclusions for the published head |
| R13 | Repository notifications | Subscribe to a relevant issue/repository and verify subscription state; delivery requires actual notification evidence. | pending |
| R14 | Stars/watchers/social metadata | Verify a deliberate owner action and read-back if desired; never manufacture third-party adoption. | pending |
| R15 | Wiki | Publish a useful troubleshooting page to the wiki repository and verify its rendered page and Git history. | pending |
| R16 | Repository insights | Inspect contributors, commits, pulse, and dependency/network information after activity exists. Record screenshots or exported responses with timestamp. | pending |
| R17 | Traffic insights | Read owner-only views/clones/referrers statistics after GitHub has data; preserve privacy and timestamps. Empty data proves access only. | pending |
| R18 | Community health | Publish contribution guidance, code of conduct, security policy, support and templates; verify GitHub's community-profile response. | pending |
| R19 | Citation metadata | Add accurate software citation metadata only if appropriate, then verify GitHub's citation rendering. | pending |
| R20 | Funding / Sponsors | Configure an actual funding destination only when supplied by the owner; verify destination and rendered funding control. Never invent beneficiaries. | pending — owner destination/account needed |

## Issues, Projects, and Discussions

[Issues](https://docs.github.com/en/issues) supports project work tracking.
[Sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
and [issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)
are separate relationships; a Markdown task list does not demonstrate either.

| ID | Feature | Minimum real use and required evidence | Status / evidence |
|---|---|---|---|
| I01 | Issue lifecycle | Track a real port defect/task, update its body, complete it, and verify final state and closing reason. | pending |
| I02 | Issue forms/templates | Submit a real test report through the configured form; preserve the issue URL and resulting fields. A YAML file alone is not execution. | pending |
| I03 | Labels | Create useful architecture/testing labels, apply them to work, verify issue label membership. | proven — [task #4](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) uses both newly created area labels; API membership verified |
| I04 | Assignees | Assign a real responsible contributor and verify assignment. Do not assign unrelated people. | proven — [assigned task #4](https://github.com/alexandru-savinov/agterm-omarchy/issues/4) has the actual owner identity; named agent claim linked above |
| I05 | Milestones | Group release criteria in a milestone, complete one item, verify progress counts. | pending — [milestone #1](https://github.com/alexandru-savinov/agterm-omarchy/milestone/1) exists with assigned work; completion progress remains unproven |
| I06 | Comments/reactions/task lists | Record a useful test observation and update a completion checklist; verify timeline and reaction/task state. | pending — [substantive coordination comments](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6506411483) proven; reaction/task-list exercise pending |
| I07 | Sub-issues | Add a real test/build child issue to the port tracking issue; query relationship and progress. | proven — [parent read-back](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/4/parent) links #4 to #1; summary correctly shows 1 child, 0 complete |
| I08 | Issue dependencies | Mark packaging blocked by verified build/testing work; query blocking and blocked-by edges, then resolve the actual prerequisite. | pending — [actual blocking edge](https://api.github.com/repos/alexandru-savinov/agterm-omarchy/issues/4/dependencies/blocked_by) and reverse read-back proven; prerequisite resolution awaits VM integration |
| I09 | Issue types | Assign a supported issue type and query it. Organization-managed types may require an organization-owned repository. | pending — ownership/API capability check |
| I10 | Linked branches and PR closing references | Link a task branch and merge a fixing PR that closes the issue; inspect timeline cross-references. | pending |
| I11 | Issue search/filtering | Retrieve an issue with label, milestone, state, and relationship filters; record query and result URL. | pending — label/state search returned #4; milestone/relationship-filter combinations remain to verify |
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
| P02 | Draft/ready transition | Start unfinished work as draft, complete readiness checks, transition to ready, and verify timeline. | pending — [PR #6](https://github.com/alexandru-savinov/agterm-omarchy/pull/6) is genuinely draft; ready transition awaits acceptance |
| P03 | PR template | Open a PR with the project template populated with actual validation and risks. | pending |
| P04 | Inline review/comment/suggestion | Review a real line, propose a useful change, apply it, and resolve the conversation with evidence. | pending |
| P05 | Requested review and independent approval | Request an authorized reviewer; obtain genuine review with reviewer identity, state, and commit SHA. | pending — separate reviewer needed |
| P06 | Requested changes and re-review | Record a genuine review defect, fix it, and obtain an updated review. Do not manufacture criticism to simulate collaboration. | pending — real review finding |
| P07 | CODEOWNERS | Add valid ownership rules, create a matching change, and verify review routing and rule interpretation. | pending — independent eligible owner needed for request |
| P08 | PR merge | Merge tested work and verify merge commit plus linked issue closure. | pending |
| P09 | Squash/rebase/merge strategies | Exercise each strategy on a useful, separate PR and verify resulting commit graph. One merge does not prove all strategies. | pending |
| P10 | Merge conflict resolution | If a real conflict occurs, resolve it and preserve conflicting base/head and resolution evidence; a documented fixture may demonstrate mechanics without harming actual work. | pending |
| P11 | Auto-merge | Enable on a PR waiting for a real required check and verify automatic merge after success. | pending |
| P12 | Branch protection | Protect a dedicated validation branch, attempt a harmless disallowed update, record denial, and complete an allowed PR path. | pending |
| P13 | Branch rulesets | Apply relevant checks/force-push restrictions and demonstrate enforcement, not just creation. | pending |
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
| A01 | Push and PR workflows | Trigger both with real commits/PRs, record run URLs, events, SHAs, and conclusions. | pending |
| A02 | Manual workflow dispatch | Dispatch a useful packaging/smoke workflow with inputs and verify recorded input/effect. | pending |
| A03 | Scheduled workflow | Observe an actual scheduled run; a manual dispatch of the same workflow does not prove the schedule. | pending — scheduled event must occur |
| A04 | Matrix jobs | Run native supported architecture/toolchain jobs and verify every matrix leg; do not label cross-compilation as runtime testing. | pending |
| A05 | Job dependencies/outputs | Feed a verified build output/version into dependent package and validation jobs. | pending |
| A06 | Cache save and restore | Capture a save in one run and an actual cache hit in a later run with the same key. | pending |
| A07 | Workflow artifacts | Upload real test/build evidence, download it independently, and verify contents/hashes. | pending |
| A08 | Job summaries/annotations | Publish meaningful test summary and actionable compiler/test annotations; verify run display. | pending |
| A09 | Reusable workflow/composite action | Invoke shared build/test logic from another workflow and verify nested run/step output. | pending |
| A10 | Variables/secrets | Read a non-sensitive variable and consume a dedicated benign test secret without printing it; verify comparison success and delete the test secret afterward. | pending |
| A11 | Workflow concurrency/cancellation | Run a deliberately bounded test group twice and verify superseded run cancellation without interrupting release work. | pending |
| A12 | Failure/retry handling | Rerun a genuinely failed job after diagnosis and verify attempt number plus corrected outcome. | pending |
| A13 | Minimal token permissions | Verify a limited-permission job performs its needed action; denied unrelated action can be checked against an isolated fixture. | pending |
| A14 | Hosted ARM64/x86_64 runners | Execute native architecture probe and build/test on each available runner, retaining `uname` and result. | pending |
| A15 | Self-hosted runner | Register a dedicated isolated runner, run a bounded trusted job, and remove registration after use. Do not expose the interactive Mac or personal VM to untrusted PR execution. | pending — isolated runner and policy needed |
| A16 | Environment/deployment | Run a job against a named test environment and verify deployment `success`, SHA, and environment URL. | pending |
| A17 | Deployment branch protection | Demonstrate rejection from a dedicated disallowed test branch and success from the permitted branch. | pending |
| A18 | Deployment reviewer/wait timer | Observe the protected wait, genuine approval where applicable, and subsequent execution. | pending — reviewer or timed gate |
| A19 | OIDC | Exchange a job identity for a narrowly scoped credential with a real provider; redact JWTs and record provider audit evidence. `id-token: write` alone is not use. | pending — external provider integration |
| A20 | Artifact attestations | Attest a real release artifact, download it, and verify provenance against repository/workflow/SHA. | pending |
| A21 | GitHub Pages | Deploy useful Omarchy documentation, fetch the public URL, and verify content version and deployment run. | pending |
| A22 | Pages custom domain | Verify an owner-supplied domain, DNS, and HTTPS certificate; do not invent or take over a domain. | pending — owned domain/DNS access |
| A23 | Release/prerelease and notes | Publish an honestly labeled tested artifact release/prerelease with notes and tag provenance. | pending |
| A24 | Release assets/download | Upload architecture-labeled tarball/checksums, download from release URL, verify checksum and launch/test it. | pending |
| A25 | Packages/container registry | Publish a useful build/test image or package with provenance, pull by digest/version, and use it. | pending — workflow `packages: write` or appropriate auth |
| A26 | Package visibility/access | Verify intended public visibility and unauthenticated consumption of the published package. | pending |

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
| S07 | Code scanning/CodeQL | Run analysis for a supported language present here, verify uploaded analysis and commit SHA in GitHub. Swift toolchain/runner support must be checked. | pending |
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
| C01 | Codespaces/dev containers | Create a workspace from committed devcontainer configuration, run a real build/check, record workspace/commit/logs, stop/delete when done. | pending — authorization scope, quota, and entitlement check |
| C02 | Codespaces port forwarding | Serve local project documentation inside that Codespace and verify the intended private forwarded endpoint. | pending — running Codespace |
| C03 | Codespaces prebuilds | Generate and consume a real prebuild, verify its log and workspace provenance. | pending — applicable repository configuration and storage budget |
| C04 | Copilot coding agent | Assign a useful bounded issue; verify a genuine GitHub agent session, generated PR, tests, and human review. | pending — entitlement/policy/credits |
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
| C16 | Marketplace/custom Action | Consume a suitable Action in a successful run; publishing a custom Action requires a genuinely reusable implementation and separate release evidence. | pending |
| C17 | Gists | Publish a useful non-sensitive standalone snippet only if it belongs outside the repository, then verify rendered content/revision. | pending — genuine standalone artifact |
| C18 | Desktop/mobile/github.dev surfaces | Perform a useful operation in each requested client and verify the resulting repository state. Equivalent clients are not separate underlying repo features. | pending — interactive client availability |
| C19 | Organization teams/roles/audit log/SSO/SCIM | Verify actual organizational administration with appropriate owner/enterprise access. No personal-repository setting substitutes for these features. | pending — no organizations accessible through current API authorization; account-wide availability unresolved |
| C20 | Enterprise policies/runners/networking/managed users | Use an explicitly authorized eligible enterprise and retain policy/enforcement evidence. | pending — no verified enterprise entitlement or administrative access |

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
