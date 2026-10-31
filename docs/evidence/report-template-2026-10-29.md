# Reusable acceptance-report template — 2026-10-29

The public [agent-report template](https://github.com/alexandru-savinov/agterm-omarchy-agent-template)
provides a small JSON report format, an attributed example, a Python standard-library validator, six boundary tests, and a validation workflow.
External testing agents can generate their own repository, identify the application source and evidence, and report what they actually observed.
The [generated owner-operated report](https://github.com/alexandru-savinov/agterm-omarchy-agent-report)
demonstrates that workflow with existing public Omarchy evidence.
Neither repository is a fork; neither changes the application fork or LFS fixture repository.

**Validator success means report structure and attribution are consistent.**
It does not authenticate an author, fetch evidence URLs, reproduce runtime tests, or establish independent approval.
The owner-generated report explicitly declares `existing_evidence_review`, `new_runtime_test=false`, and `independent_account_review=false`.
Its four reported passes refer to existing installed-runtime, corrected-cleanup, core-test, and binary-provenance evidence.
This is owner-operated infrastructure, not third-party adoption or a new runtime execution.

## Reuse

Use the template repository's **Use this template** control, update `report.json`, and run:

```sh
python3 scripts/test_validate_report.py
python3 scripts/validate_report.py report.json
```

The workflow runs on pushes to main, pull requests, or manual dispatch.
It uses pinned Actions with `contents: read`, disables checkout credential persistence, and publishes a JSON outcome artifact and fixed job summary.
Report text is never executed, and the validator performs no network requests.
The [README](https://github.com/alexandru-savinov/agterm-omarchy-agent-template/blob/0e6e5f105390e1f41fd69da6e0db7ac69b334673/README.md)
explains attribution rules, privacy limits, and onboarding through the public hub/agent protocol.

## Actual template generation

Both requested names returned HTTP 404 before creation.
GitHub's API then confirmed the source repository is public, `fork=false`, and `is_template=true`.
The generated repository was created through GitHub's documented
[`POST /repos/{template_owner}/{template_repo}/generate`](https://docs.github.com/en/rest/repos/repos#create-a-repository-using-a-template),
not by manually copying files into a new remote.

| Identity | Verified value |
|---|---|
| Template source | [`0e6e5f105390e1f41fd69da6e0db7ac69b334673`](https://github.com/alexandru-savinov/agterm-omarchy-agent-template/commit/0e6e5f105390e1f41fd69da6e0db7ac69b334673) |
| Generated initial commit | [`6fbf5ce5577ffd2be61b9e38558f61109c1434b3`](https://github.com/alexandru-savinov/agterm-omarchy-agent-report/commit/6fbf5ce5577ffd2be61b9e38558f61109c1434b3) |
| Initial file tree in both repositories | `d765ae80826065c144e59c4424993dd76753889c` |
| Generated initial history | One commit; unrelated template-generation history |
| Generated repository metadata | `template_repository.full_name=alexandru-savinov/agterm-omarchy-agent-template`, `fork=false`, public |
| Populated report commit | [`f6866def704b9f8d7f88702b407b2f7147301f8a`](https://github.com/alexandru-savinov/agterm-omarchy-agent-report/commit/f6866def704b9f8d7f88702b407b2f7147301f8a) |

The generated repository retains a [template-origin record](https://github.com/alexandru-savinov/agterm-omarchy-agent-report/blob/f6866def704b9f8d7f88702b407b2f7147301f8a/template-origin.json).
Independent API read-back verified the template relationship; matching initial trees verify the transferred content.
The generated report then added accurate attribution and a fourth existing-evidence check.

## Executed validation and public rendering

The [template's initial workflow](https://github.com/alexandru-savinov/agterm-omarchy-agent-template/actions/runs/40957450588)
passed at its exact source commit.
The [generated report workflow](https://github.com/alexandru-savinov/agterm-omarchy-agent-report/actions/runs/40957722238)
passed at populated report commit `f6866def704b9f8d7f88702b407b2f7147301f8a`.

[Artifact 12934805296](https://github.com/alexandru-savinov/agterm-omarchy-agent-report/actions/runs/40957722238/artifacts/12934805296)
was downloaded independently and its result checked against the local report bytes, source commit, run ID, report ID, activity, and four check records.
The result contains `validator_success=true` and `new_runtime_test=false`.
Its extracted `validation.json` SHA256 is `fe915a631dff9cebc624c05ba7b35cf1e34f4ab4760116414ab333ee3ca927d7`;
the report SHA256 is `66200f2ecb5041c3490ff470bd9951835000a63d9f21e6a5df1ea21bbd630b0c`.
GitHub's archive digest is `sha256:a912a82417b21d17cc4cde2bc3f2955357411ae4a5b3c2cf283e51951bcf5ef0`.

Rendered README HTML was retrieved for both public repositories and verified to contain the intended title, command examples, and onboarding links.
Local validation included all six tests, the actual report validator, Actionlint, and `git diff --check`.
No private state, credentials, application code, paid service, or billing setting was introduced.
[Task #34](https://github.com/alexandru-savinov/agterm-omarchy/issues/34) holds the coordination and proof.
