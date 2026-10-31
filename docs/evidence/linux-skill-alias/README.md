# Shared skill ancestor alias validation evidence

Task: #106, review wave #92.
Inspected source: `8458e16d06b2432cd6895955b6be972b1edae93d`.
Status: source-confirmed control-flow defect; **not a runtime reproduction**.
Swift was unavailable, so proposed tests have not been compiled or executed.
Existing Linux integration owners retain implementation ownership.
This directory is inert evidence and does not change active source or test targets.

## Trigger and expected consequence

Both `~/.claude` and `~/.codex` are symbolic links to one shared agent root.
That shared root already contains either an outdated managed `skills/agterm/SKILL.md`, or an existing `skills/` directory with no `agterm` leaf.
The source skill contains the managed marker and newer content.

The planner emits one replacement followed by validation of the duplicate alias.
The replacement changes the contents covered by the second operation's saved directory fingerprint.
The service's initial preflight succeeds, but the second operation then reports a stale preview after installation has succeeded.
The apply result is unsuccessful even though the shared skill was updated; this is a reported partial failure, not evidence that the update was rolled back.

## Source trace

- [planSkill](https://github.com/alexandru-savinov/agterm-omarchy/blob/8458e16d06b2432cd6895955b6be972b1edae93d/agterm-linux/Sources/LinuxIntegrations/IntegrationService%2BPlans.swift#L170) resolves each display path and allows overwriting a managed skill.
  The first target emits `replaceDirectory`; the duplicate destination branch appends `validate` to the same operation list.
- [validationAnchor and nearestExistingAncestor](https://github.com/alexandru-savinov/agterm-omarchy/blob/8458e16d06b2432cd6895955b6be972b1edae93d/agterm-linux/Sources/LinuxIntegrations/IntegrationFilesystem.swift#L80) return the existing lexical leaf, or the existing `skills/` parent for a missing leaf.
  These are directories reached through an ancestor symlink, rather than symlinks themselves.
- [fingerprint](https://github.com/alexandru-savinov/agterm-omarchy/blob/8458e16d06b2432cd6895955b6be972b1edae93d/agterm-linux/Sources/LinuxIntegrations/IntegrationFilesystem.swift#L33) recursively hashes directory entries and file contents.
  Updating or adding the skill changes that digest.
- [service apply](https://github.com/alexandru-savinov/agterm-omarchy/blob/8458e16d06b2432cd6895955b6be972b1edae93d/agterm-linux/Sources/LinuxIntegrations/IntegrationService.swift#L23) first validates every operation, then applies each in order.
  Filesystem apply rechecks `.validate`, catches the stale condition through the service loop, and records an unsuccessful operation result.

The existing `agentRootSkillAliases` test creates no `skills/` parent, so its nearest existing anchor is the agent-root symlink itself.
Its link fingerprint stays unchanged when descendants are installed.
Existing shared-leaf symlink tests similarly hash the link target string rather than updated directory contents.
They do not cover the two directory-anchor cases above.

## Proposal and limits

`proposal.patch` sends duplicate alias validations to the existing `currentTargetValidations` list, already prepended before writes.
It adds the proposed Swift regression source also copied here as a `.swift.txt` artifact.
The parameterized case covers both an existing outdated skill and an existing parent with a missing leaf.
A second test checks that retargeting the ancestor to a different skill after preview rejects the plan before replacing the shared original.

This is a narrow ordering proposal, not a transaction or concurrency fix.
Moving checks earlier must not be represented as solving TOCTOU races.
The current service already separates preflight from mutations and is nontransactional.
A concurrent alias change after its check can still escape this proposal's validation.
The integration owner should assess that existing concurrency boundary before adoption.

Actual validation: source trace, fixture review and `git apply --check` passed.
No Swift runtime, native integration, installation, VM, workflow dispatch or release validation was performed.
See [REPLAY.md](REPLAY.md) for unexecuted baseline/fixed test commands.
Same-account agent evidence is not independent approval.
