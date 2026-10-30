# Bounded Bandit code-scanning integration

`bandit-sarif.yml` is a separate manual analysis of real Python helpers at the workflow's exact reviewed `main` SHA.
It leaves default CodeQL, branch protection, application CI, and release gates unchanged.
Pull requests run only integration syntax checks; root reviews and authorizes the actual manual scan/upload.

The explicit production-helper scope is:

- `scripts/github/channel_probe.py`
- `scripts/github/queue.py`
- `scripts/prepare-omarchy-prerelease.py`
- `scripts/zmx-patches/check.py`
- `docs/omarchy-pages/build.py`

No personal files, dependency trees, fixtures, tests, Swift sources, or private directories are scanned.
Each input must be a tracked regular file inside the clean checkout; its SHA-256 and the source commit/ref are retained.
Changing the scope requires a code review of `scripts/security/bandit_sarif.py`.

The disposable hosted virtual environment installs released `bandit[sarif]==1.9.4` from PyPI.
The scanner wheel is checked against SHA-256 `f89ffa663767f5a0585ea075f01020207e966a9c0f2b9ef56a57c7963a3f6f8e`.
Transitive dependencies are resolved at installation time; the complete pip install report retains their download URLs,
versions and archive hashes, with `pip freeze --all` and Python/pip versions alongside it.
This records dependency provenance without claiming every dependency was pinned in advance.

Bandit's built-in SARIF formatter produces `bandit.raw.sarif`.
All severities/confidences are retained and `--ignore-nosec` disables inline suppression; no baseline or skip list is used.
The upload copy adds only `automationDetails.id` for category `bandit-orchestration-packaging`.
It does not invent, remove or rewrite findings.
Scanner exit 1 is recorded as findings-present, while execution/parse/skipped-file errors fail the job.
Successful ingestion is an integration result, not a statement that findings are harmless or fixed.

The upload step alone receives the ephemeral `GH_TOKEN`.
The job has `contents: read` and `security-events: write`; no package/repository write permission or custom scanner action is needed.
It uploads the actual gzip/base64 SARIF through GitHub's supported API, records the receipt, polls completion with a two-minute bound,
and verifies the completed analysis's tool/version, category, commit/ref and result count.
Raw SARIF, complete scanner output, source hashes, dependency provenance, upload receipt/status and analysis object are retained as an Actions artifact.
The repository's code-scanning UI also retains the actual findings for triage.

Treat findings as scanner signals requiring code/context review, not automatically confirmed vulnerabilities or CVEs.
An empty actual report can prove integration, but cannot prove remediation.
Do not dismiss alerts or suppress output merely to make this exercise green.

Official references: [Bandit SARIF extra](https://bandit.readthedocs.io/en/latest/start.html),
[released Bandit 1.9.4 metadata](https://pypi.org/pypi/bandit/1.9.4/json), and
[GitHub SARIF upload/analysis API](https://docs.github.com/en/rest/code-scanning/code-scanning#upload-an-analysis-as-sarif-data).
