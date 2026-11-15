# Pull request machine fields

Every non-draft pull request starts its body with four lines, one `Name: value` per line, in any order:

```text
Task: #123
Agent: your-agent-name
Reviewed head: <the full 40-character sha the review covered>
Review: https://github.com/<owner>/<repo>/...
```

They must be the **first non-blank lines of the body**. The block ends at the first blank line or the first line that is not one of the four fields, and everything after it is ignored. This is deliberate: the checker never tries to guess what Markdown would hide or render (code fences, HTML comments, HTML blocks), so there is nothing to forge there.

The `PR fields` workflow fails a pull request when a field is missing, repeated or malformed, or when `Reviewed head` differs from the pull request's current head. Push after a review and the check fails until the review is redone and the field updated. It runs when a PR is opened, edited, pushed to, reopened, marked ready for review or converted back to draft.

## What the check is not

- **Advisory.** It is not in the required-checks ruleset, and a pull request can edit the workflow that runs it (only the checker script is read from the base commit). A green `PR fields` is never evidence that a review is current; read the review.
- **Not yet enforcing.** Until a second, reviewed commit sets `ENFORCED_FROM` in `scripts/github/pr_fields.py` (tracked in [#423](https://github.com/alexandru-savinov/agterm-omarchy/issues/423)), every problem is only a warning. After it, pull requests opened before the cutoff still get warnings; the comparison uses the pull request's `created_at` from the event payload, which the author cannot set.

## Known limits

- Draft pull requests are skipped, so a pull request can stay a draft to avoid the check.
- Pull requests whose author is a bot account are skipped.
- Values are checked for format only: the `Task` issue and the `Review` link are not fetched, and `Reviewed head` is self-reported.
