# Completed feature-proof follow-up wave

[Root opened this successor](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6589557229) after all four predecessor waves reached terminal ledgers and PR194 merged.
Root alone writes the ledger.
All three assignments are delivered, at revision9 with zero reservations; the peak was two reservations within a capacity of three worker turns plus root.
Monitors use those same slots.

- scanner_review reproduced27 scanner boundary checks and delivered [the pinned PR202 review](https://github.com/alexandru-savinov/agterm-omarchy/pull/202#pullrequestreview-5744587660).
  Root explicitly rebase-merged the reviewed correction; `pr202-rebase-proof.json` records the real request, successful response, one-parent result and unchanged frozen candidate.
- retry_audit completed [the bounded78-run audit](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6590516983), which correctly found no later attempt at its observation time.
  Root subsequently retried one diagnosed ARM64 failure; `a12-retry-proof.json` records attempt2 success at the identical source and checkout.
  Its verified-cache recovery does not establish repair of the original external download route.
- feature_apply delivered the four-file P09/A12 ledger and Pages update in [PR204](https://github.com/alexandru-savinov/agterm-omarchy/pull/204), exact head53bdfb4b8c8f7362a56601df0bb17ef281863a44.
  Worker and root each passed18 Pages tests; all121 criteria/order and119 unrelated rows were preserved, with72 proven/45 pending/4 unavailable.
  Root completed the requested non-force publication takeover after permission-review timeouts; the worker verified the result and posted [its completed handoff](https://github.com/alexandru-savinov/agterm-omarchy/issues/4#issuecomment-6593210392).

`terminal-replay.json` exactly replays all51 events across five waves, including stored summaries.
The five waves record15 delivered assignments and17 claim/start attempts, including two coordinated worker retries, with peak reservations3/3/2/3/2 and zero terminal reservations.
The requested500-worker run remains distinct from these actual scoped deliveries.
Supplemental reviews and the later CI-job retry are not additional worker assignments or worker retries.
Same-account work/review is not independent-account approval, and ledger observations do not establish continuous process liveness.

The candidate `omarchy` branch stays frozen atbec375d70025ce3ec0e93fb9a0f3bb3be5ecae9d.
The [native-candidate evidence bundle](native-candidate/README.md) records full trusted CI41532553668, both unchanged validated archives, packaged inventories and39 synthetic operator checks.
Native execution remains pending because the VM is stopped and automated startup failed; no new guest stage or frontend has been executed.
These documentation changes may advance main without changing the frozen candidate, installed app or earlier accepted6775 evidence.

```sh
python3 scripts/github/waves.py summary docs/waves/2026-11-02-wave-05/ledger.json --limit 20
```
