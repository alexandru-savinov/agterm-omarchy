# Pull request machine fields

Every non-draft pull request carries four lines, outside code blocks and HTML comments:

```text
Task: #123
Agent: your-agent-name
Reviewed head: <the full 40-character sha the review covered>
Review: https://github.com/<owner>/<repo>/...
```

The `PR fields` workflow fails a pull request when a field is missing, repeated or malformed, or when `Reviewed head` differs from the pull request's current head. Push after a review and the check fails until the review is redone and the field updated.

## What the check is not

- **Advisory.** It is not in the required-checks ruleset, and a pull request can edit the workflow that runs it (only the checker script is read from the base commit). A green `PR fields` is never evidence that a review is current; read the review.
- Pull requests opened before the cutoff `ENFORCED_FROM` in `scripts/github/pr_fields.py` get a warning instead of a failure. The cutoff compares the pull request's `created_at` from the event payload.

## Known limits

- Draft pull requests are skipped, so a pull request can stay a draft to avoid the check.
- Pull requests whose author is a bot account are skipped.
- Values are checked for format only: the `Task` issue and the `Review` link are not fetched, and `Reviewed head` is self-reported.
