"""Read the terminal's actual face from the GTK process after launch and a family-only rename."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from xml.sax.saxutils import escape

from atspi_smoke import BIN, control_json, launch, stop, wait_for, window_list, window_tree


FAMILIES = ("DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono")


def font_fixture(root):
    # Keep the generic monospace face fixed independently of the Omarchy terminal config.
    directory = root / "fonts"
    directory.mkdir()
    faces = {}
    for family in FAMILIES:
        match = subprocess.check_output(
            ["fc-match", "--format", "%{family}\t%{file}", f"{family}:style=Book"],
            text=True, timeout=10)
        matched_family, file = match.split("\t", 1)
        assert family in matched_family.split(","), f"runner lacks {family}, found {match}"
        face = directory / Path(file).name
        shutil.copyfile(file, face)
        faces[family] = face

    fontconfig = root / "fonts.conf"
    fontconfig.write_text(f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>{escape(str(directory))}</dir>
  <cachedir>{escape(str(root / "font-cache"))}</cachedir>
  <match target="pattern">
    <test name="family" qual="any"><string>monospace</string></test>
    <edit name="family" mode="prepend_first" binding="strong">
      <string>{FAMILIES[0]}</string>
    </edit>
  </match>
</fontconfig>
""", encoding="utf-8")
    return fontconfig, faces


def mapped_faces(process, fixture_faces):
    assert process.poll() is None, "agterm exited before the font was observed"
    return {Path(parts[5]) for line in Path(f"/proc/{process.pid}/maps").read_text().splitlines()
            if len(parts := line.split(maxsplit=5)) == 6 and Path(parts[5]) in fixture_faces}


def verify_omarchy_font_family(env):
    root = Path(env["AGTERM_STATE_DIR"])
    fontconfig, faces = font_fixture(root)
    # CI builds both variants. Exercise the shipping Swift build when present.
    release = Path(BIN).parent.parent / "release" / Path(BIN).name
    binary = str(release) if release.is_file() else BIN
    failures = []

    for index, source in enumerate(("Omarchy", "Settings", "scoped Ghostty config")):
        home = root / f"h{index}"
        state = root / f"s{index}"
        state.mkdir()
        theme = home / ".local/state/omarchy/current/theme"
        theme.mkdir(parents=True)
        (theme / "ghostty.conf").write_text("background = #315286\n", encoding="utf-8")
        config = home / ".config/ghostty/config"
        config.parent.mkdir(parents=True)
        config.write_text(f'font-family = "{FAMILIES[1]}"\n', encoding="utf-8")
        settings = {"restoreMode": "none", "welcomeShown": True}
        if source == "Settings":
            settings["fontFamily"] = FAMILIES[2]
        elif source == "scoped Ghostty config":
            scoped = state / "config/ghostty.conf"
            scoped.parent.mkdir()
            scoped.write_text(f'font-family = "{FAMILIES[2]}"\n', encoding="utf-8")
        (state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        socket = state / "a.sock"
        local = dict(env, HOME=str(home), AGTERM_STATE_DIR=str(state),
                     AGTERM_CONTROL_SOCKET=str(socket), FONTCONFIG_FILE=str(fontconfig))
        process, _ = launch(local, binary=binary)
        try:
            wait_for(socket.is_socket, f"{source}: control socket did not start")
            window = wait_for(lambda: next((row["id"] for row in window_list(local) if row["open"]), None),
                              f"{source}: no window opened")
            session = window_tree(local, window)["workspaces"][0]["sessions"][0]["id"]

            def write_sample(identifier):
                reply = control_json(local, "session", "type", "printf 'Typeface sample 0123 gjy\\n'\n",
                                     "--target", identifier, "--window", window, "--json")
                assert reply["ok"], reply

            def expect_face(label, family):
                wanted = faces[family]
                mapped = wait_for(lambda: wanted in mapped_faces(process, faces.values()),
                                  "", timeout=6, required=False)
                actual = sorted(face.name for face in mapped_faces(process, faces.values()))
                if mapped:
                    print(f"OK: {label} mapped {wanted.name} in PID {process.pid}", flush=True)
                else:
                    failure = f"{label} expected {wanted.name}, mapped {actual}"
                    print(f"FAIL: {failure}", flush=True)
                    failures.append(failure)

            write_sample(session)
            expect_face(f"{source} launch", FAMILIES[1 if index == 0 else 2])
            if source != "Omarchy":
                continue

            assert faces[FAMILIES[2]] not in mapped_faces(process, faces.values()), (
                "the changed face was already mapped before the family edit")
            replacement = config.with_name("config.next")
            replacement.write_text(f'font-family = "{FAMILIES[2]}"\n', encoding="utf-8")
            replacement.replace(config)
            # Allow the observer's 250 ms debounce before opening a new session.
            time.sleep(1)
            if faces[FAMILIES[2]] in mapped_faces(process, faces.values()):
                print("OK: the existing terminal mapped the changed face before opening a new session", flush=True)
            response = control_json(local, "session", "new", "--name", "Changed family",
                                    "--window", window, "--json")
            assert response["ok"], response
            write_sample(response["result"]["id"])
            expect_face("Omarchy family-only rename and new session", FAMILIES[2])
        finally:
            stop(process)
    assert not failures, "\n".join(failures)
