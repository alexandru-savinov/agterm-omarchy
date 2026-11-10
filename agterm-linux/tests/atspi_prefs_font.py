"""An unavailable font family is displayed as Default but never rewritten by merely opening Preferences.

The combo has no row for a family the system lacks, so it shows index 0; only a real selection may change
settings.json. A rendered-glyph check of the chosen family is native-only and is not attempted here.
"""

import time

from atspi_smoke import NEGATIVE_SETTLE_SECONDS, collect, launch, stop, wait_for, window_list
from atspi_prefs_combo import (choose_combo, close_preferences, open_preferences, preferences_row,
                               read_settings, seed_settings, settings_path)

MISSING = "Nonexistent Mono"


def font_row_value(app):
    """The label the Font row shows as its current value, or None while the row is unbuilt."""
    row = preferences_row(app, "Font")
    if row is None:
        return None
    values = [item.get_name() for item in collect(row, role="label") if item.get_name() not in ("", "Font")]
    return values[0] if values else None


def verify_unavailable_font_family(env):
    seed_settings(env, fontFamily=MISSING)
    process, app = launch(env)
    try:
        wait_for(lambda: window_list(env), "the window never registered")
        time.sleep(NEGATIVE_SETTLE_SECONDS)
        with open(settings_path(env), "rb") as source:
            before = source.read()
        assert read_settings(env).get("fontFamily") == MISSING, "the launch already dropped the missing family"

        open_preferences(app, process.pid, "Appearance")
        wait_for(lambda: font_row_value(app) == "Default",
                 f"the Font row shows {font_row_value(app)!r} for a missing family, not 'Default'")
        time.sleep(NEGATIVE_SETTLE_SECONDS)
        with open(settings_path(env), "rb") as source:
            assert source.read() == before, "opening Appearance rewrote settings.json"

        row = preferences_row(app, "Font")
        families = [item.get_name() for item in collect(row, role="label")]
        choose_combo(env, app, process.pid, "Font", ["Default"], 1,
                     lambda settings: settings.get("fontFamily") not in (None, MISSING))
        chosen = read_settings(env)["fontFamily"]
        close_preferences(app, process.pid)
        print(f"measured: chose available family {chosen!r} (row labels before: {families})")

        stop(process)
        process = None
        process, app = launch(env)
        open_preferences(app, process.pid, "Appearance")
        wait_for(lambda: font_row_value(app) == chosen,
                 f"after a relaunch the Font row shows {font_row_value(app)!r}, not the persisted {chosen!r}")
        print("OK: a missing font family shows as Default, is not rewritten, and a real pick persists")
    finally:
        if process is not None:
            stop(process)
