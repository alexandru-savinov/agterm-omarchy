# Browser clear runtime probe

The `html-overlay` AT-SPI scenario reuses `atspi_html_v0351.py` to exercise the real saved WebKit store.
Run the normal Debug smoke runner with disposable state:

```sh
AGTERM_ATSPI_SCENARIO=html-overlay scripts/test-linux-ui.sh
```

The scenario keeps the existing open-page refusal, persistent/ephemeral storage and cookie isolation checks.
It also retains PR215's synthetic failure/data-preservation check separately from the real WebKit error path.
Its three injected operations are `hold`, `fail` and `cancel`, followed by a normal retry.

- `hold` reports that `clearing` is set, then waits for the test-owned release marker before starting WebKit.
  The competing clear must be refused before that marker is written.
  A ten-second monotonic limit fails the probe if the release is lost.
- `fail` completes synthetically, preserving the earlier data-retention assertion.
- `cancel` passes an already-cancelled `GCancellable` to `webkit_website_data_manager_clear`.
  Its actual callback calls `clear_finish`, copies/frees the `GError`, resets the flag and replies.
  WebKit may remove data before returning a cancelled GTask, so this case records that state rather than
  treating cancellation as a rollback guarantee.
  A subsequent persistent open seeds fresh data, and a normal clear must remove it.

The captured app-stderr records are scoped to the test app PID and fresh per-operation IDs.
Every admitted operation must report one start and one completion.
The real operations must report one native callback, including the cancelled error result.
The synthetic failure must report no native callback.
No scheduling sleep establishes admission or concurrency.

`AGTERM_ATSPI_BROWSER_CLEAR` and `AGTERM_ATSPI_BROWSER_CLEAR_RELEASE` are private Debug test controls.
They require an AT-SPI scenario and are inert in Release; they add no control command, user setting or event.
The probe exercises Debug WebKit behavior, not shipping Release or native Wayland acceptance.
No real browser profile, installed app or owner session is opened or cleared.
