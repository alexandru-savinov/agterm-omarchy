# Corrective implementation wave: 2026-11-02

[Root's handoff](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6580863851) opened this successor after all wave02 review turns ended and its ledger reached revision 9 with zero reservations.
This is the sole active worker ledger; earlier waves remain terminal records.

| Assignment | Worker | Public claim |
| --- | --- | --- |
| Bound generated config retention | `config_isolation` | [#192](https://github.com/alexandru-savinov/agterm-omarchy/issues/192#issuecomment-6580882813) |
| Implement the isolated native config probe | `native_probe` | [#199](https://github.com/alexandru-savinov/agterm-omarchy/issues/199#issuecomment-6580899252) |

The initial ledger is revision 4, with two reported running assignments and two reservations against a maximum of three worker slots.
Root is additional to those slots and is applying the small notice metadata correction, integrating outputs, and preparing the native operator.
No monitor-only worker or third worker is claimed.
The requested budget here is two concrete assignments; the three resumed waves contain eight assignments altogether.
Neither count asserts 500 launches or imports historical research-worker results.

Root recorded the already posted working claims when this ledger was written; those timestamps are observation times, not process-start measurements.
The offline engine validates each transition, path reservation, lease and expected revision; it does not launch or authenticate workers.
Root alone writes this ledger in one checkout.
Completion remains distinct from review, merge and exact native acceptance.
Accepted `6775c336` artifacts and their native evidence remain unchanged.

## Coordinated recovery and delivered correction

After delayed public checkpoints, root [coordinated a stop](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6582719540) and [confirmed both agent turns were interrupted](https://github.com/alexandru-savinov/agterm-omarchy/issues/193#issuecomment-6582735818).
The existing branches and uncommitted probe files were preserved.
Retry events released the original reservations before the same workers claimed new sessions, `cfg192c3` and `np199r2m8`.
These are second attempts of the same two assignments, not additional assignments or completed workers.

The config correction is delivered at [fdf8bad2](https://github.com/alexandru-savinov/agterm-omarchy/commit/fdf8bad2cee3389a97bcefc73a8e1ad3065fb3cd), with [nine helper tests and a pending real-libghostty Linux fixture](https://github.com/alexandru-savinov/agterm-omarchy/issues/192#issuecomment-6582798583).
At revision 11, one assignment is completed and the probe assignment retains its reservation.
Root still owes corrected-head review and exact native acceptance.

```sh
python3 scripts/github/waves.py summary docs/waves/2026-11-02-wave-03/ledger.json --limit 20
```
