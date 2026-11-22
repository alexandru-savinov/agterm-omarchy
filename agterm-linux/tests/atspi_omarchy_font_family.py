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
  <!-- Fontconfig 2.18's 48-spacing and 49-sansserif rules add monospace
       to Ghostty's spaced query, even when it names an explicit family. -->
  <match target="pattern">
    <test name="spacing" compare="eq"><int>100</int></test>
    <edit name="family" mode="append"><string>monospace</string></edit>
  </match>
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


def replace_alias(template, destination, family, mtime=None):
    replacement = destination.with_name("fonts.conf.next")
    rule = template.read_text(encoding="utf-8").replace(
        f"<string>{FAMILIES[0]}</string>", f"<string>{family}</string>")
    replacement.write_text(rule, encoding="utf-8")
    if mtime is not None:
        os.utime(replacement, ns=(mtime, mtime))
    replacement.replace(destination)


def verify_omarchy_font_family(env):
    root = Path(env["AGTERM_STATE_DIR"])
    fontconfig, faces = font_fixture(root)
    # CI builds both variants. Exercise the shipping Swift build when present.
    release = Path(BIN).parent.parent / "release" / Path(BIN).name
    binary = str(release) if release.is_file() else BIN
    failures = []

    sources = ("Omarchy", "Settings", "scoped Ghostty config", "Fontconfig alias", "same-second Fontconfig")
    for index, source in enumerate(sources):
        home = root / f"h{index}"
        state = root / f"s{index}"
        state.mkdir()
        theme = home / ".local/state/omarchy/current/theme"
        theme.mkdir(parents=True)
        (theme / "ghostty.conf").write_text("background = #315286\n", encoding="utf-8")
        config = home / ".config/ghostty/config"
        config.parent.mkdir(parents=True)
        fontconfig_only = source in ("Fontconfig alias", "same-second Fontconfig")
        family = "monospace" if fontconfig_only else FAMILIES[1]
        config.write_text(f'font-family = "{family}"\n', encoding="utf-8")
        profile_fontconfig = state / "fonts.conf"
        shutil.copyfile(fontconfig, profile_fontconfig)
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
                     AGTERM_CONTROL_SOCKET=str(socket), FONTCONFIG_FILE=str(profile_fontconfig))
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

            def assert_existing_face(label, family):
                wanted = faces[family]
                assert wait_for(lambda: wanted in mapped_faces(process, faces.values()), "",
                                timeout=6, required=False), (
                    f"{label}: existing terminal expected {wanted.name}, "
                    f"mapped {sorted(face.name for face in mapped_faces(process, faces.values()))}")
                print(f"OK: {label}: existing terminal mapped {wanted.name} before opening a new session", flush=True)

            def reload():
                response = control_json(local, "config", "reload", "--json")
                assert response["ok"], response

            write_sample(session)
            initial = FAMILIES[0] if fontconfig_only else FAMILIES[1] if source == "Omarchy" else FAMILIES[2]
            expect_face(f"{source} launch", initial)
            if source == "same-second Fontconfig":
                # Pin two successive writes to distinct nanoseconds in the same second as the
                # initial file. This exercises real stat calls without scheduling-sensitive sleeps.
                second = profile_fontconfig.stat().st_mtime_ns // 1_000_000_000
                reload()
                for offset, changed_family in enumerate(FAMILIES[1:], 1):
                    assert faces[changed_family] not in mapped_faces(process, faces.values())
                    stamp = second * 1_000_000_000 + offset * 100_000_000
                    replace_alias(fontconfig, profile_fontconfig, changed_family, stamp)
                    assert profile_fontconfig.stat().st_mtime_ns == stamp
                    reload()
                    assert_existing_face(f"{source} reload {offset}", changed_family)
                continue
            if source != "Omarchy" and not fontconfig_only:
                continue

            assert faces[FAMILIES[2]] not in mapped_faces(process, faces.values()), (
                f"{source}: the changed face was already mapped before the font edit")
            if source == "Omarchy":
                replacement = config.with_name("config.next")
                replacement.write_text(f'font-family = "{FAMILIES[2]}"\n', encoding="utf-8")
                replacement.replace(config)
                label = "Omarchy family-only rename and new session"
            else:
                # Fontconfig compares mtimes at seconds granularity against the time it loaded.
                time.sleep(1.1)
                replace_alias(fontconfig, profile_fontconfig, FAMILIES[2])
                reload()
                label = "Fontconfig alias rename and config reload"
            assert_existing_face(source, FAMILIES[2])
            response = control_json(local, "session", "new", "--name", "Changed family",
                                    "--window", window, "--json")
            assert response["ok"], response
            write_sample(response["result"]["id"])
            expect_face(label, FAMILIES[2])
        finally:
            stop(process)
    assert not failures, "\n".join(failures)
