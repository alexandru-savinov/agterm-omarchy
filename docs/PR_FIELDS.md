# Pull request machine fields

Every non-draft pull request starts its body with four lines, one `Name: value` per line, in any order:

```text
Task: #123
Agent: your-agent-name
Reviewed head: <the full 40-character sha the review covered>
Review: https://github.com/<owner>/<repo>/...
```

They must be the **first non-blank lines of the body**. A line belongs to the block only if it is exactly a field name, a colon, one space and a value. The block ends at the first blank line or the first line that does not fit that shape (including a field with no space after the colon), and everything after it is ignored. A trailing tab or a doubled space after the colon stays in the block and is reported as an invalid value. This is deliberate: the checker never tries to guess what Markdown would hide or render (code fences, HTML comments, HTML blocks), so there is nothing to forge there.

The `PR fields` workflow fails a pull request when a field is missing, repeated or malformed, or when `Reviewed head` differs from the pull request's current head. Push after a review and the check fails until the review is redone and the field updated. It runs when a PR is opened, edited, pushed to, reopened, marked ready for review or converted back to draft.

## What the check is not

- **Advisory.** It is not in the required-checks ruleset, and a pull request can edit the workflow that runs it (only the checker script is read from the base commit). A green `PR fields` is never evidence that a review is current; read the review.
- **Not yet enforcing.** Until a second, reviewed commit sets `ENFORCED_FROM` in `scripts/github/pr_fields.py` (tracked in [#423](https://github.com/alexandru-savinov/agterm-omarchy/issues/423)), every problem is only a warning. After it, pull requests opened before the cutoff still get warnings; the comparison uses the pull request's `created_at` from the event payload, which the author cannot set.

## Known limits

- **Self-attestation only.** Typing the pull request's current head sha into `Reviewed head` passes without any new review, and `Review` accepts any github.com URL from any repository. The `Task` issue and the `Review` link are not fetched.
- **Bot exemption.** A pull request opened through a GitHub App token counts as bot-authored and is skipped, and so is a branch a bot opened that a human later pushes to.
- **Base without a checker.** A pull request aimed at a base branch that has no checker passes with a notice.
- **Drafts are skipped,** so a pull request can stay a draft to avoid the check.
- **A workflow re-run reuses the original event payload.** It checks the body and head as they were at the first run; edit the body or push to get a fresh check.
