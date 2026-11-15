Task: #
Agent:
Reviewed head:
Review:

<!-- Machine fields: keep these four lines first in the body, one per line, with a space after each colon. Task: the issue number. Agent: your name.
Reviewed head: the full 40-character sha the review covered; update it after every push.
Review: link to the review comment. Advisory check by the "PR fields" workflow on non-draft pull requests; see docs/PR_FIELDS.md. -->

## Problem and resulting behavior

Describe the concrete problem and what changes for the user.

## Validation

List the checks actually run, with architecture and display backend. Link CI or attach sanitized VM evidence.
Explain any failed, skipped, or unavailable check.

- [ ] Installer/script tests for packaging changes
- [ ] Shared core and Linux frontend tests for Swift changes
- [ ] Native Omarchy/Hyprland smoke test for rendering or input changes
- [ ] Control API and documentation updated when behavior changes

## Compatibility

Describe changes to dependencies, persisted state, terminal defaults, or upstream compatibility; write “none” if none.
