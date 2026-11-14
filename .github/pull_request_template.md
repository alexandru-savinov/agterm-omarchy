Task: #
Agent:
Reviewed head:
Review:

<!-- Machine fields, one per line, outside code blocks. Task: the issue number. Agent: your name.
Reviewed head: the full 40-character sha the review covered; update it after every push.
Review: link to the review comment. Checked by the "PR fields" workflow on non-draft pull requests. -->

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
