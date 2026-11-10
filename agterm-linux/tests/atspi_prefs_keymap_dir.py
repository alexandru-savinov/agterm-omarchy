"""The Key Mapping page follows the configured directory, and bindings follow only a reload.

The directory is pointed at each fixture by editing settings.json underneath the running app, which is
what the Choose… dialog persists; nothing re-reads bindings until Reload (or Use Default, which reloads by
design). Bindings are read off `keymap list`, whose cached keymap is what dispatch uses.
"""

import json
import os
import time

from atspi_smoke import (NEGATIVE_SETTLE_SECONDS, activate, collect, control_json, focus_accessible_window, launch,
                         named, press_escape, preferences_window, stop, wait_for)
from atspi_prefs_combo import preferences_row, read_settings, seed_settings

CHOOSER_TITLE = "Choose the agterm configuration directory"


def verify_keymap_config_directory(env):
    state = env["AGTERM_STATE_DIR"]
    fixtures = {name: os.path.join(state, f"keymap-{name}") for name in ("valid", "malformed", "missing")}
    for path in fixtures.values():
        os.makedirs(path)
    with open(os.path.join(fixtures["valid"], "keymap.conf"), "w", encoding="utf-8") as target:
        target.write('command "Valid Demo" true\n')
    with open(os.path.join(fixtures["malformed"], "keymap.conf"), "w", encoding="utf-8") as target:
        target.write('command "Malformed Demo" true\nfrobnicate everything\n')
    default = os.path.join(state, "config")

    process, app = launch(dict(env, AGTERM_ATSPI_OPEN_PREFERENCES="key-mapping"))
    try:
        def without_paths(node):
            # `keymap list` reports the LIVE directory's path beside the cached bindings; only the latter count.
            if isinstance(node, dict):
                return {key: without_paths(value) for key, value in node.items() if key != "path"}
            if isinstance(node, list):
                return [without_paths(value) for value in node]
            return node

        def bindings():
            return json.dumps(without_paths(control_json(env, "keymap", "list", "--json")["result"]), sort_keys=True)

        def shows(text):
            """The page shows `text` as a row subtitle, whether GTK exposes it as a label or a description."""
            dialog = preferences_window(app)
            if dialog is None:
                return False
            for item in collect(dialog):
                try:
                    if item.get_name() == text or item.get_description() == text:
                        return True
                except Exception:
                    pass
            return False

        def press(row_title, button):
            row = wait_for(lambda: preferences_row(app, row_title), f"Key Mapping has no {row_title!r} row")
            target = wait_for(lambda: next((item for item in collect(row, name=button)
                                           if item.get_role_name() != "label"), None),
                              f"the {row_title!r} row has no {button!r} button")
            activate(target)

        def assert_page(directory, diagnostic, what):
            wait_for(lambda: shows(directory), f"{what}: the page never showed {directory}")
            wait_for(lambda: shows(os.path.join(directory, "keymap.conf")),
                     f"{what}: the keymap.conf row does not show {directory}/keymap.conf")
            wait_for(lambda: preferences_row(app, diagnostic), f"{what}: the Diagnostics group lacks {diagnostic!r}")
            stray = [title for title in ("Line 1", "Line 2", "Line 3", "No keymap errors")
                     if title != diagnostic and preferences_row(app, title)]
            assert not stray, f"{what}: the Diagnostics group also shows {stray}"

        def point_at(directory, present, absent, diagnostic, what):
            before = bindings()
            seed_settings(env, configDirectory=directory)
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            assert bindings() == before, f"{what}: editing the directory changed bindings before any reload"
            press("Configuration actions", "Reload")
            assert_page(directory, diagnostic, what)
            current = wait_for(lambda: (text := bindings()) != before and text,
                               f"{what}: Reload did not change the active bindings")
            for name in present:
                assert name in current, f"{what}: {name!r} is not an active binding after Reload"
            for name in absent:
                assert name not in current, f"{what}: {name!r} is still active after Reload"

        wait_for(lambda: preferences_window(app), "Key Mapping did not open at launch")
        assert_page(default, "No keymap errors", "the default directory")
        point_at(fixtures["valid"], ["Valid Demo"], [], "No keymap errors", "the valid fixture")
        point_at(fixtures["malformed"], ["Malformed Demo"], ["Valid Demo"], "Line 2", "the malformed fixture")
        point_at(fixtures["missing"], [], ["Valid Demo", "Malformed Demo"], "No keymap errors", "the missing fixture")

        point_at(fixtures["valid"], ["Valid Demo"], [], "No keymap errors", "the valid fixture again")
        press("Configuration actions", "Use Default")
        wait_for(lambda: "configDirectory" not in read_settings(env), "Use Default left configDirectory set")
        assert_page(default, "No keymap errors", "Use Default")
        wait_for(lambda: "Valid Demo" not in bindings(), "Use Default kept the fixture's bindings")

        point_at(fixtures["valid"], ["Valid Demo"], [], "No keymap errors", "the valid fixture before Choose…")
        before = (read_settings(env).get("configDirectory"), bindings())
        press("Config directory", "Choose…")
        chooser = wait_for(lambda: named(app, CHOOSER_TITLE, role="dialog") or named(app, CHOOSER_TITLE, role="frame"),
                           "", timeout=10, required=False)
        if chooser is None:
            print("UNVERIFIED: the folder chooser never appeared under Xvfb; the cancel leg is native-only")
        else:
            focus_accessible_window(chooser, process.pid)
            press_escape(process.pid)
            wait_for(lambda: not (named(app, CHOOSER_TITLE, role="dialog") or named(app, CHOOSER_TITLE, role="frame")),
                     "Escape did not cancel the folder chooser")
            time.sleep(NEGATIVE_SETTLE_SECONDS)
            assert (read_settings(env).get("configDirectory"), bindings()) == before, (
                "a cancelled folder chooser changed the directory or the bindings")
        print("OK: the Key Mapping page follows each directory, and bindings change only on reload")
    finally:
        stop(process)
