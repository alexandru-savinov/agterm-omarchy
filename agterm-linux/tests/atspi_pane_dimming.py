"""Actual X11 pixels and PTY input; not proof of desktop-composited window alpha."""

from collections import Counter
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from atspi_smoke import (control_json, launch, press_return, stop, type_x11_text,
                         wait_for, window_list, window_tree)


def verify_pane_dimming(env, state):
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", state)) / "pane-dimming"
    artifacts.mkdir(parents=True, exist_ok=True)
    records = []
    for opacity in (1.0, 0.5):
        for strength in (0, 5):
            root = Path(state) / f"dimming-{opacity}-{strength}"
            root.mkdir()
            settings = root / "settings.json"
            settings.write_text(json.dumps({"backgroundOpacity": opacity,
                                           "inactivePaneMuteStrength": strength, "restoreMode": "none"}))
            local = dict(env, AGTERM_STATE_DIR=str(root), AGTERM_CONTROL_SOCKET=str(root / "agterm.sock"))
            specimen = root / "specimen.py"
            specimen.write_text(
                "import os,sys,pathlib\n"
                "color,ready,received=sys.argv[1:]\n"
                "os.write(1, ('\\x1b]11;'+color+'\\x07\\x1b[2J\\x1b[H\\x1b[?25l').encode())\n"
                "pathlib.Path(ready).write_text('ready')\n"
                "for line in sys.stdin: pathlib.Path(received).write_text(line.strip())\n")
            process, _app = launch(local)
            try:
                window = wait_for(lambda: window_list(local), "no dimming window")[0]["id"]
                session = control_json(local, "session", "new", "--window", window,
                                       "--name", "Pixel dimming", "--json")["result"]["id"]

                def cli(*args):
                    result = control_json(local, *args, "--target", session, "--window", window, "--json")
                    assert result["ok"], result
                    return result

                def overlay_visible():
                    return next(item for workspace in window_tree(local, window)["workspaces"]
                                for item in workspace["sessions"] if item["id"] == session).get("overlay")

                cli("session", "split", "on")
                for pane, color in (("left", "#ff0000"), ("right", "#00ff00")):
                    command = shlex.join(["/usr/bin/python3", str(specimen), color,
                                          str(root / (pane + "-ready")), str(root / (pane + "-input"))])
                    cli("session", "type", command + "\n", "--pane", pane)
                    wait_for(lambda: (root / (pane + "-ready")).exists(), f"{pane} PTY specimen did not run")
                wid = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--pid", str(process.pid)],
                                              text=True).splitlines()[-1]
                sequence = 0

                def pixels(label):
                    nonlocal sequence
                    sequence += 1
                    path = artifacts / f"{opacity}-{strength}-{sequence}-{label}.png"
                    subprocess.run(["import", "-window", wid, str(path)], check=True, timeout=10)
                    raw = subprocess.check_output(["convert", str(path), "-alpha", "off", "-depth", "8", "rgb:-"],
                                                  timeout=10)
                    counts = Counter(zip(raw[0::3], raw[1::3], raw[2::3]))
                    colors = []
                    for channel in (0, 1):
                        candidates = [(count, rgb) for rgb, count in counts.items()
                                      if rgb[channel] > max(rgb[(channel + 1) % 3], rgb[(channel + 2) % 3]) + 40]
                        count, rgb = max(candidates, default=(0, (0, 0, 0)))
                        assert count > 2000, f"{label}: no substantial rendered {'red' if channel == 0 else 'green'} patch"
                        colors.append(rgb[channel])
                    return colors

                def settled(label):
                    previous = pixels(label)
                    def stable():
                        nonlocal previous
                        time.sleep(0.15)
                        current = pixels(label)
                        matched = current == previous
                        previous = current
                        return current if matched else None
                    return wait_for(stable, f"{label}: rendered colors never settled")

                def focus(pane):
                    cli("session", "focus", "primary" if pane == "left" else "split")
                    marker = root / (pane + "-input")
                    token = f"keyboard-{pane}-{sequence}"
                    type_x11_text(token, process.pid)
                    press_return(process.pid)
                    wait_for(lambda: marker.exists() and marker.read_text() == token,
                             f"keyboard did not reach the {pane} specimen")
                    return settled(pane)

                left = focus("left")
                right = focus("right")
                if strength == 0:
                    assert left == right, f"zero mute changed pixels on focus: {left}, {right}"
                else:
                    assert left[0] > right[0] + 15 and right[1] > left[1] + 15, (
                        f"inactive pane did not visibly dim at opacity {opacity}: {left}, {right}")
                assert focus("left") == left, "focus reversal did not restore exact terminal contrast"
                before = settings.read_bytes()
                for cover in ("quick", "floating"):
                    if cover == "quick":
                        assert control_json(local, "quick", "show", "--json")["ok"]
                        wait_for(lambda: window_tree(local, window).get("quickVisible"), "quick did not open")
                    else:
                        cli("session", "overlay", "open", "sleep 120", "--size-percent", "40")
                        wait_for(overlay_visible, "floating overlay did not become visible in the session")
                    covered = settled(cover)
                    if strength == 0:
                        assert covered == left, f"zero mute cover changed patch colors: {covered}, {left}"
                    else:
                        assert covered[0] < left[0] - 15, f"{cover} did not dim the active terminal: {covered}, {left}"
                    if cover == "quick":
                        assert control_json(local, "quick", "hide", "--json")["ok"]
                        wait_for(lambda: not window_tree(local, window).get("quickVisible"), "quick did not hide")
                    else:
                        cli("session", "overlay", "close")
                        wait_for(lambda: not overlay_visible(), "floating overlay did not close")
                    assert focus("left") == left, f"{cover} dismissal did not restore contrast"
                zoom_target = f"surface:{session}:primary"
                assert control_json(local, "surface", "zoom", "show", "--target", zoom_target, "--json")["ok"]
                wait_for(lambda: window_tree(local, window).get("zoomedSurface"), "primary zoom did not engage")
                assert control_json(local, "surface", "zoom", "hide", "--target", zoom_target, "--json")["ok"]
                wait_for(lambda: not window_tree(local, window).get("zoomedSurface"), "primary zoom did not clear")
                assert focus("left") == left, "zoom exit did not restore split contrast"
                assert settings.read_bytes() == before, "transient focus/cover operations changed settings"
                records.append({"opacity": opacity, "strength": strength, "left": left, "right": right})
            finally:
                stop(process)
    (artifacts / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    print("OK: actual X11 split focus and quick/floating cover pixels restore; zero-mute controls pass. "
          "Desktop-composited translucency and per-pane overlay-wash pixels remain unverified.")
