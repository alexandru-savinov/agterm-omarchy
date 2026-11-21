"""Read the same terminal's painted background across Omarchy theme publications."""

import os
from pathlib import Path
import signal
import subprocess

from atspi_smoke import control_json, launch, stop, wait_for
from x11_capture import capture


def verify_omarchy_theme(env):
    state = Path(env["AGTERM_STATE_DIR"])
    current = Path(env["HOME"]) / ".local/state/omarchy/current"
    directory = current / "theme"
    directory.mkdir(parents=True)
    generation = current / "theme.generation"
    theme_a, theme_b, theme_c, scoped_color = "315286", "793759", "396B4F", "524475"

    def colors(background):
        return f"background = #{background}\nforeground = #FFFFFF\n"

    (directory / "ghostty.conf").write_text(colors(theme_a), encoding="utf-8")
    generation.write_text("1\n", encoding="utf-8")
    # Omarchy ignores XDG_STATE_HOME, so a conflicting directory there must be ignored.
    alternate = state / "xdg/omarchy/current/theme"
    alternate.mkdir(parents=True)
    (alternate / "ghostty.conf").write_text(colors("FF0000"), encoding="utf-8")
    scoped = state / "config/ghostty.conf"
    scoped.parent.mkdir()
    local = dict(env, XDG_STATE_HOME=str(state / "xdg"))

    process, _ = launch(local)
    try:
        window = subprocess.check_output(
            ["xdotool", "search", "--sync", "--onlyvisible", "--pid", str(process.pid)],
            text=True, timeout=8).splitlines()[-1]
        raw_geometry = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", window], text=True, timeout=5)
        geometry = dict(line.split("=", 1) for line in raw_geometry.splitlines())
        # Sample the lower right of the terminal, well inside its text area and away from the prompt.
        x = int(geometry["X"]) + int(geometry["WIDTH"]) * 3 // 4
        y = int(geometry["Y"]) + int(geometry["HEIGHT"]) * 3 // 4

        def expect(background, stage):
            expected = bytes.fromhex(background)
            last = None

            def painted():
                nonlocal last
                assert process.poll() is None, f"agterm exited during {stage}"
                last = capture().new_subpixbuf(x, y, 1, 1).get_pixels()[:3]
                return last == expected

            if not wait_for(painted, "", required=False):
                raise AssertionError(f"{stage}: terminal pixel was #{last.hex()}, expected #{background}")
            print(f"OK: Omarchy terminal pixel #{background} after {stage}", flush=True)

        expect(theme_a, "launch")

        staged = current / "next-theme"
        staged.mkdir()
        (staged / "ghostty.conf").write_text(colors(theme_b), encoding="utf-8")
        directory.rename(current / "previous-theme")
        staged.rename(directory)
        generation.write_text("2\n", encoding="utf-8")
        expect(theme_b, "directory replacement and generation bump")

        replacement = directory / "ghostty.conf.new"
        replacement.write_text(colors(theme_c), encoding="utf-8")
        replacement.replace(directory / "ghostty.conf")
        expect(theme_c, "atomic edit in the replacement directory")

        scoped.write_text(colors(scoped_color), encoding="utf-8")
        os.kill(process.pid, signal.SIGUSR2)
        expect(scoped_color, "SIGUSR2 reload of the scoped override")

        scoped.unlink()
        result = control_json(local, "theme", "set", "Dracula", "--json")
        assert result["ok"], result
        expect("282A36", "explicit agterm theme selection")
        (directory / "ghostty.conf").write_text(colors(theme_a), encoding="utf-8")
        generation.write_text("3\n", encoding="utf-8")
        os.kill(process.pid, signal.SIGUSR2)
        expect("282A36", "Omarchy generation bump with an explicit agterm theme")
        scoped.write_text(colors(scoped_color), encoding="utf-8")
        os.kill(process.pid, signal.SIGUSR2)
        expect(scoped_color, "scoped override of an explicit agterm theme")
    finally:
        stop(process)
