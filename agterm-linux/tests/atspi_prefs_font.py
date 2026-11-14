"""An unavailable font family is displayed as Default but never rewritten by merely opening Preferences.

The combo has no row for a family the system lacks, so it shows index 0; only a real selection may change
settings.json. Rendered glyphs are compared with fresh same-display references, never cross-host golden images.
"""

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import time

from gi.repository import PangoCairo

from atspi_smoke import NEGATIVE_SETTLE_SECONDS, collect, control_json, launch, stop, wait_for, window_list
from atspi_prefs_combo import (choose_combo, close_preferences, open_preferences, preferences_row,
                               read_settings, seed_settings, settings_path)

MISSING = "Nonexistent Mono"


def row_texts(row):
    """Every name and description under the row: where GTK publishes a combo's current value varies."""
    texts = []
    for item in collect(row):
        try:
            texts += [item.get_name() or "", item.get_description() or ""]
        except Exception:
            pass
    return [text for text in texts if text and text != "Font"]


def font_row_value(app):
    """The value the Font row shows, or None while the row is unbuilt."""
    row = preferences_row(app, "Font")
    texts = row_texts(row) if row is not None else []
    return texts[0] if texts else None


def font_row_shows(app, value):
    row = preferences_row(app, "Font")
    return row is not None and value in row_texts(row)



BACKGROUND = bytes.fromhex("123456")
SPECIMEN = r"""
import os, pathlib, signal, sys
os.write(1, b"\x1b]11;#123456\x07\x1b]10;#ffffff\x07\x1b[2J\x1b[H\x1b[?25l"
            b"Hamburgefontsiv 0123456789 iIl1 MW@#%\r\n")
pathlib.Path(sys.argv[1]).write_text("written")
while True:
    signal.pause()
"""


def start_specimen(env, label):
    state = Path(env["AGTERM_STATE_DIR"])
    script, ready = state / "font-specimen.py", state / f"{label}.ready"
    script.write_text(SPECIMEN)
    ready.unlink(missing_ok=True)
    window = wait_for(lambda: window_list(env), "the window never registered")[0]["id"]
    created = control_json(env, "session", "new", "--name", label, "--window", window, "--json")
    assert created["ok"], created
    typed = control_json(env, "session", "type", shlex.join(["/usr/bin/python3", str(script), str(ready)]) + "\n",
                         "--target", created["result"]["id"], "--window", window, "--json")
    assert typed["ok"], typed
    wait_for(ready.exists, "the real PTY did not write the font specimen")


def specimen_window(process):
    windows = subprocess.check_output(
        ["xdotool", "search", "--onlyvisible", "--pid", str(process.pid)], text=True, timeout=10).splitlines()
    # A PID may own auxiliary X windows. Ask the window manager for its active toplevel,
    # then verify ownership and visibility instead of choosing an arbitrary search result.
    active = subprocess.check_output(["xdotool", "getactivewindow"], text=True, timeout=10).strip()
    owner = subprocess.check_output(["xdotool", "getwindowpid", active], text=True, timeout=10).strip()
    assert owner == str(process.pid), f"active window {active} belongs to PID {owner}, expected {process.pid}"
    assert active in windows, f"active specimen window {active} is not visible among {windows}"
    return active


def glyph_pixels(process, destination):
    subprocess.run(["import", "-window", specimen_window(process), str(destination)], check=True, timeout=10)
    width, height = map(int, subprocess.check_output(
        ["convert", str(destination), "-format", "%w %h", "info:"], text=True, timeout=10).split())
    pixels = subprocess.check_output(
        ["convert", str(destination), "-alpha", "off", "-depth", "8", "rgb:-"], timeout=10)
    assert len(pixels) == width * height * 3
    # OSC 11 gives only the terminal a unique background. Discover its bounds from pixels, not
    # AT-SPI's absent terminal role or a fixed window/sidebar size. Exclude all GTK chrome.
    rows = [pixels[y * width * 3:(y + 1) * width * 3] for y in range(height)]
    spans = [(y, row.find(BACKGROUND), row.rfind(BACKGROUND)) for y, row in enumerate(rows)]
    spans = [(y, left // 3, right // 3) for y, left, right in spans
             if left >= 0 and left % 3 == 0 and right % 3 == 0 and right - left > width * 3 // 2]
    if not spans:
        return None
    top, bottom = spans[0][0], spans[-1][0]
    left, right = min(item[1] for item in spans), max(item[2] for item in spans)
    points = [(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
              if rows[y][x * 3:x * 3 + 3] != BACKGROUND]
    if not points:
        return None
    x0, x1 = min(x for x, _ in points), max(x for x, _ in points)
    y0, y1 = min(y for _, y in points), max(y for _, y in points)
    return (x1 - x0 + 1, y1 - y0 + 1,
            b"".join(rows[y][x0 * 3:(x1 + 1) * 3] for y in range(y0, y1 + 1)))


def stable_glyphs(process, destination, expected=None):
    previous, matches = None, 0

    def observed():
        nonlocal previous, matches
        pixels = glyph_pixels(process, destination)
        matches = matches + 1 if pixels is not None and pixels == previous else 0
        previous = pixels
        return pixels if matches >= 2 and (expected is None or pixels == expected) else None

    result = wait_for(observed, "rendered glyphs did not stabilize at the expected font", timeout=15)
    print(f"measured: {destination.name}: {result[:2]} RGB {hashlib.sha256(result[2]).hexdigest()}")
    return result

def verify_unavailable_font_family(env):
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", env["AGTERM_STATE_DIR"]))
    artifacts.mkdir(parents=True, exist_ok=True)
    families = sorted({family.get_name() for family in PangoCairo.FontMap.get_default().list_families()
                       if family.is_monospace()})
    assert families, "no installed monospace family is available for a rendered reference"
    references = {}
    for index, family in enumerate([MISSING, None] + families):
        seed_settings(env, fontFamily=family, fontSize=18, restoreMode="none")
        reference, _ = launch(env)
        try:
            start_specimen(env, f"font-reference-{index}")
            references[family] = stable_glyphs(reference, artifacts / f"font-reference-{index}.png")
        finally:
            stop(reference)
        if family not in (MISSING, None) and references[family] != references[MISSING]:
            break
    chosen_reference = next((family for family in families if family in references
                             and references[family] != references[MISSING]), None)
    assert chosen_reference, "installed families provided no distinct glyph reference; rendering is unverified"
    print("measured: missing family matches references " + repr(
        [family for family, pixels in references.items() if family != MISSING and pixels == references[MISSING]]))
    seed_settings(env, fontFamily=MISSING)
    process, app = launch(env)
    try:
        wait_for(lambda: window_list(env), "the window never registered")
        start_specimen(env, "font-existing")
        stable_glyphs(process, artifacts / "font-existing-before.png", references[MISSING])
        time.sleep(NEGATIVE_SETTLE_SECONDS)
        with open(settings_path(env), "rb") as source:
            before = source.read()
        assert read_settings(env).get("fontFamily") == MISSING, "the launch already dropped the missing family"

        open_preferences(app, process.pid, "Appearance")
        if not wait_for(lambda: font_row_shows(app, "Default"), "", required=False):
            row = preferences_row(app, "Font")
            shape = [(item.get_role_name(), item.get_name()) for item in collect(row)] if row else None
            raise AssertionError(f"the Font row shows {row_texts(row) if row else None} for a missing family, "
                                 f"not 'Default'; row subtree {shape}")
        time.sleep(NEGATIVE_SETTLE_SECONDS)
        with open(settings_path(env), "rb") as source:
            assert source.read() == before, "opening Appearance rewrote settings.json"

        row_labels = row_texts(preferences_row(app, "Font"))
        choose_combo(env, app, process.pid, "Font", ["Default"] + families, families.index(chosen_reference) + 1,
                     lambda settings: settings.get("fontFamily") == chosen_reference)
        chosen = read_settings(env)["fontFamily"]
        close_preferences(app, process.pid)
        stable_glyphs(process, artifacts / "font-existing-after.png", references[chosen])
        start_specimen(env, "font-new")
        stable_glyphs(process, artifacts / "font-new.png", references[chosen])
        print(f"measured: chose available family {chosen!r} (row labels before: {row_labels})")

        stop(process)
        process = None
        process, app = launch(env)
        open_preferences(app, process.pid, "Appearance")
        if not wait_for(lambda: font_row_shows(app, chosen), "", required=False):
            raise AssertionError(f"after a relaunch the Font row shows {row_texts(preferences_row(app, 'Font'))}, "
                                 f"not the persisted {chosen!r}")
        print("OK: a missing font family shows as Default, is not rewritten, and a real pick persists and updates existing/new rendered glyphs")
    finally:
        if process is not None:
            stop(process)
