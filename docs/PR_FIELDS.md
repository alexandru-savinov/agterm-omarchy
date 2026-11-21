# Pull request machine fields

Every non-draft pull request carries four lines, one `Name: value` per line, in any order:

```text
Task: #123
Agent: your-agent-name
Reviewed head: <the full 40-character sha the review covered>
Review: https://github.com/<owner>/<repo>/...
```

They go in one of two places:

- **The Review record fold**, as the [pull request template](../.github/pull_request_template.md) lays it out at the bottom of the body. The fold opens with a line that is exactly `<details><summary>Review record</summary>`; the four lines are the first non-blank lines after it, and `</details>` is the next non-blank line after them. A body may have only one such fold. An unclosed fold, an indented or renamed opener, or any other line between the opener and the fields reads as missing.
- **The first non-blank lines of the body.** Older pull requests and the automated sync pull requests written by `scripts/upstream-sync.py` use this position.

A field found in both places counts as repeated. A line belongs to a block only if it is exactly a field name, a colon, one space and a value. A block ends at the first blank line or the first line that does not fit that shape (including a field with no space after the colon). A trailing tab or a doubled space after the colon stays in the block and is reported as an invalid value. Everything outside the two blocks is ignored. This is deliberate: the checker never tries to guess what Markdown would hide or render (code fences, HTML comments, HTML blocks), so there is nothing to forge there.

The `PR fields` workflow reports a problem when a field is missing, repeated or malformed, or when `Reviewed head` differs from the pull request's current head. Until the cutoff described below is set (issue #423) it only warns and the check stays green, so a green `PR fields` says nothing about whether a review is current. Once the cutoff is set, a push after a review makes the check fail until the review is redone and the field updated. It runs when a PR is opened, edited, pushed to, reopened, marked ready for review or converted back to draft.

For a PR labelled `upstream-offer`, the lead also verifies a green `Hosted macOS checks` result from
`.github/workflows/omarchy-macos.yml` on the same head before sending changes upstream.
The required repository check `CI` comes from `.github/workflows/omarchy-ci.yml`.

## What the check is not

- **Advisory.** It is not in the required-checks ruleset, and a pull request can edit the workflow that runs it (only the checker script is read from the base commit). A green `PR fields` is never evidence that a review is current; read the review.
- **Not yet enforcing.** Until a second, reviewed commit sets `ENFORCED_FROM` in `scripts/github/pr_fields.py` (tracked in [#423](https://github.com/alexandru-savinov/agterm-omarchy/issues/423)), every problem is only a warning. After it, pull requests opened before the cutoff still get warnings; the comparison uses the pull request's `created_at` from the event payload, which the author cannot set.

## Known limits

- **Self-attestation only.** Typing the pull request's current head sha into `Reviewed head` passes without any new review, and `Review` accepts any github.com URL from any repository. The `Task` issue and the `Review` link are not fetched.
- **Bot exemption.** A pull request opened through a GitHub App token counts as bot-authored and is skipped, and so is a branch a bot opened that a human later pushes to.
- **Base without a checker.** A pull request aimed at a base branch that has no checker passes with a notice.
- **Drafts are skipped,** so a pull request can stay a draft to avoid the check.
- **A workflow re-run reuses the original event payload.** It checks the body and head as they were at the first run; edit the body or push to get a fresh check.
