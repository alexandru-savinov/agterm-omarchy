# Replay on a supported Swift host

These commands were **not executed** by the evidence author.
They create disposable worktrees and run tests using the fixture's temporary home, not a real agent installation.
Use a trusted checkout with the pinned source object available and the repository's required Swift/dependency toolchain.
Do not substitute the user's actual home or integration paths.

From the evidence branch checkout:

```bash
repo=$(git rev-parse --show-toplevel)
evidence="$repo/docs/evidence/linux-skill-alias"
replay=$(mktemp -d)
git -C "$repo" worktree add --detach "$replay/baseline" 8458e16d06b2432cd6895955b6be972b1edae93d
git -C "$repo" worktree add --detach "$replay/proposal" 8458e16d06b2432cd6895955b6be972b1edae93d
cp "$evidence/IntegrationServiceExistingAliasTests.swift.txt" \
  "$replay/baseline/agterm-linux/Tests/LinuxIntegrationsTests/IntegrationServiceExistingAliasTests.swift"
git -C "$replay/proposal" apply --check "$evidence/proposal.patch"
git -C "$replay/proposal" apply "$evidence/proposal.patch"
swift test --package-path "$replay/baseline/agterm-linux" --filter existingAgentRootSkillAliases
swift test --package-path "$replay/proposal/agterm-linux" --filter existingAgentRootSkillAliases
swift test --package-path "$replay/proposal/agterm-linux" --filter retargetedExistingAgentRootSkillAlias
swift test --package-path "$replay/proposal/agterm-linux" --filter IntegrationServiceTests
```

Forecast from source, not observed test results: the baseline parameterized test reports failed success assertions after the copy; both proposed cases pass after ordering changes.
The retargeting case should reject before changing the original shared skill.
Record compiler version, commands, exit codes and complete diagnostic output; run required lint before adoption.
A compilation error or a different runtime outcome supersedes this source forecast and must be investigated.
Review the worktrees before removing them through `git worktree remove`; retain logs outside them if needed.
