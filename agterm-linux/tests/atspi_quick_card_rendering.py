"""Rendered quick-terminal content containment and outward shadow at minimum/large sizes."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from atspi_smoke import (control_json, launch, named, press_return, stop, type_x11_text,
                         wait_for, window_extents, window_list, window_tree)


def verify_quick_card_rendering(env, state):
    artifacts = Path(os.environ.get("AGTERM_UI_ARTIFACT_DIR", state)) / "quick-card-rendering"
    artifacts.mkdir(parents=True, exist_ok=True)
    cyan = (0, 255, 255)
    markers = ((255, 0, 0), (0, 255, 0), (255, 255, 0), (255, 0, 255))
    for toolbar in ("normal", "hidden"):
        reference_shadow = None
        for width, height in ((1400, 860), (1, 1)):
            tag = f"{toolbar}-{width}"
            root = Path(state) / tag
            root.mkdir()
            (root / "settings.json").write_text(json.dumps({
                "toolbarMode": toolbar, "inactivePaneMuteStrength": 0, "backgroundOpacity": 1,
            }))
            local = dict(env, AGTERM_STATE_DIR=str(root), AGTERM_CONTROL_SOCKET=str(root / "agterm.sock"))
            ready = root / "ready"
            specimen = root / "specimen.py"
            specimen.write_text(
                "import os,sys,pathlib,signal\n"
                "if len(sys.argv)>2:\n"
                " os.write(1,b'\\x1b]11;#28343c\\x07\\x1b[2J\\x1b[H\\x1b[?25l'); pathlib.Path(sys.argv[1]).write_text('ready')\n"
                " while True: signal.pause()\n"
                "size=os.get_terminal_size(); rows,cols=size.lines,size.columns\n"
                "s='\\x1b]11;#00ffff\\x07\\x1b[2J\\x1b[H\\x1b[?25l'\n"
                "for row,col,color in ((1,cols//2,'255;0;0'),(rows,cols//2,'0;255;0'),"
                "(rows//2,1,'255;255;0'),(rows//2,cols,'255;0;255')):\n"
                " s+=f'\\x1b[{row};{col}H\\x1b[48;2;{color}m \\x1b[0m'\n"
                "os.write(1,s.encode()); pathlib.Path(sys.argv[1]).write_text(f'{cols}x{rows}')\n"
                "while True: signal.pause()\n")
            process, app = launch(local)
            try:
                window = wait_for(lambda: window_list(local), "no quick-card window")[0]["id"]
                control_json(local, "window", "resize", window, "--width", str(width), "--height", str(height), "--json")
                session = next(row["id"] for workspace in window_tree(local, window)["workspaces"]
                               for row in workspace["sessions"] if row["active"])
                backdrop_ready = root / "backdrop-ready"
                backdrop_command = shlex.join(["/usr/bin/python3", str(specimen), str(backdrop_ready), "backdrop"])
                control_json(local, "session", "type", backdrop_command + "\n", "--target", session,
                             "--window", window, "--json")
                wait_for(lambda: backdrop_ready.exists(), "static backdrop PTY did not run")
                control_json(local, "quick", "show", "--json")
                card = wait_for(lambda: named(app, "Quick terminal"), "quick card did not map")
                command = shlex.join(["/usr/bin/python3", str(specimen), str(ready)])
                type_x11_text(command, process.pid)
                press_return(process.pid)
                wait_for(lambda: ready.exists() and ready.read_text(), "quick PTY specimen did not run")
                xid = subprocess.check_output(["xdotool", "getactivewindow"], text=True, timeout=10).strip()
                owner = int(subprocess.check_output(["xdotool", "getwindowpid", xid], text=True, timeout=10))
                assert owner == process.pid, f"capture belongs to PID {owner}, expected {process.pid}"

                def capture(label):
                    path = artifacts / f"{tag}-{label}.png"
                    subprocess.run(["import", "-window", xid, str(path)], check=True, timeout=10)
                    dimensions = tuple(map(int, subprocess.check_output(
                        ["convert", str(path), "-format", "%w %h", "info:"], text=True, timeout=10).split()))
                    raw = subprocess.check_output(
                        ["convert", str(path), "-alpha", "off", "-depth", "8", "rgb:-"], timeout=10)
                    assert len(raw) == dimensions[0] * dimensions[1] * 3
                    return dimensions, raw

                def stable_capture(label):
                    previous = None
                    matches = 0
                    def stable():
                        nonlocal previous, matches
                        time.sleep(0.15)
                        current = capture(label)
                        matches = matches + 1 if current == previous else 0
                        previous = current
                        return current if matches >= 2 else None
                    return wait_for(stable, f"{tag}: {label} pixels never settled")

                dimensions, shown = stable_capture("shown")
                card_box = window_extents(card)
                assert card_box, "quick card has no accessible allocation"
                allocation = (card_box.width, card_box.height)
                control_json(local, "quick", "hide", "--json")
                hidden_dimensions, hidden = stable_capture("hidden")
                assert dimensions == hidden_dimensions, "hiding the card resized its window"
                pixels = list(zip(shown[0::3], shown[1::3], shown[2::3]))
                positions = {color: [(i % dimensions[0], i // dimensions[0]) for i, value in enumerate(pixels)
                                     if value == color] for color in (cyan, *markers)}
                assert positions[cyan], "quick terminal's cyan background was not rendered"
                remaining = set(positions[cyan])
                pending = [remaining.pop()]
                while pending:
                    px, py = pending.pop()
                    for neighbor in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            pending.append(neighbor)
                assert not remaining, f"{tag}: multiple cyan components make the rendered card ambiguous"
                left = min(x for x, _ in positions[cyan])
                right = max(x for x, _ in positions[cyan])
                top = min(y for _, y in positions[cyan])
                bottom = max(y for _, y in positions[cyan])
                # GTK accessibility and X11 captures have different CSD origins. Compare actual rendered
                # extent with allocation size, independently of placement, allowing only the existing 1px border.
                assert (right - left + 1, bottom - top + 1) == (allocation[0] - 2, allocation[1] - 2), (
                    f"{tag}: rendered terminal extent does not fill the card interior: "
                    f"{(left, top, right, bottom)} vs {allocation}")
                assert all(left <= px <= right and top <= py <= bottom
                           for color in markers for px, py in positions[color]), (
                    f"{tag}: colored terminal edge cells escaped the rendered interior")
                marker_sizes = []
                for color in markers:
                    points = positions[color]
                    assert points, f"{tag}: terminal edge marker {color} was clipped away"
                    marker_width = max(px for px, _ in points) - min(px for px, _ in points) + 1
                    marker_height = max(py for _, py in points) - min(py for _, py in points) + 1
                    assert len(points) == marker_width * marker_height, f"{tag}: edge marker {color} is partially clipped"
                    marker_sizes.append((marker_width, marker_height))
                assert len(set(marker_sizes)) == 1, f"{tag}: edge cells are clipped differently: {marker_sizes}"

                def pixel(raw, px, py):
                    assert 0 <= px < dimensions[0] and 0 <= py < dimensions[1], (
                        f"{tag}: pixel sample {(px, py)} lies outside capture {dimensions}")
                    offset = (py * dimensions[0] + px) * 3
                    return tuple(raw[offset:offset + 3])

                corners = ((left + 1, top + 1), (right - 1, top + 1),
                           (left + 1, bottom - 1), (right - 1, bottom - 1))
                assert all(pixel(shown, *point) != cyan for point in corners), (
                    f"{tag}: rounded content clipping absent at {corners}")
                shadow_points = ((left - 3, (top + bottom) // 2), (right + 3, (top + bottom) // 2),
                                 ((left + right) // 2, bottom + 3))
                for point in shadow_points:
                    assert sum(pixel(shown, *point)) < sum(pixel(hidden, *point)) - 15, (
                        f"{tag}: outside-card shadow absent at {point}")
                # Each unchanged backdrop identifies its own interior edge; the sidebar and terminal
                # may have different colors, and isolated CSD decoration pixels do not extend the run.
                mid_y = (top + bottom) // 2
                left_edge, right_edge = left - 3, right + 3
                while left_edge > 0 and pixel(hidden, left_edge - 1, mid_y) == pixel(hidden, left - 3, mid_y):
                    left_edge -= 1
                while right_edge + 1 < dimensions[0] and pixel(hidden, right_edge + 1, mid_y) == pixel(hidden, right + 3, mid_y):
                    right_edge += 1
                assert left_edge < left - 3 and right_edge > right + 3, (
                    f"{tag}: no continuous side backdrops outside the card")
                edge_points = ((left_edge + 1, mid_y), (right_edge - 1, mid_y))
                shadow_profiles = []
                for edge, origin, direction in ((edge_points[0][0], left, -1), (edge_points[1][0], right, 1)):
                    available = abs(edge - origin)
                    backdrop = pixel(hidden, origin + direction * 3, mid_y)
                    samples = []
                    for distance in range(3, available + 1):
                        point = (origin + direction * distance, mid_y)
                        assert pixel(hidden, *point) == backdrop, f"{tag}: shadow scan crossed backdrop colors"
                        samples.append((distance, sum(backdrop) - sum(pixel(shown, *point))))
                    # Ignore <=2 RGB-sum quantization levels; allow a two-pixel antialiased tail shift.
                    dark = [distance for distance, delta in samples if delta > 2]
                    assert dark, f"{tag}: no measurable side shadow"
                    reach = max(dark)
                    shadow_profiles.append({"available": available, "reach": reach, "backdrop": backdrop,
                                            "edge_delta": samples[-1][1]})
                    if reference_shadow is None:
                        assert reach + 2 < available and all(delta == 0 for _, delta in samples[-2:]), (
                            f"{tag}: large-case shadow has no clear baseline before the backdrop edge")
                    else:
                        reference = reference_shadow[len(shadow_profiles) - 1]
                        assert backdrop == reference["backdrop"], f"{tag}: large/minimum backdrops differ"
                        assert abs(reach - min(reference["reach"], available)) <= 2, (
                            f"{tag}: shadow extent {reach} differs from large profile {reference} at margin {available}")
                        if available > reference["reach"] + 2:
                            assert all(delta == 0 for _, delta in samples[-2:]), (
                                f"{tag}: shadow did not clear the available backdrop margin")
                        elif available < reference["reach"] - 2:
                            assert samples[-1][1] > 2, f"{tag}: truncated shadow did not reach its backdrop edge"
                if reference_shadow is None:
                    reference_shadow = shadow_profiles
                record = {"case": tag, "client": dimensions, "card_allocation": allocation, "tty": ready.read_text(),
                          "cyan_bounds": [left, top, right, bottom], "marker_sizes": marker_sizes,
                          "shadow": [(point, pixel(shown, *point), pixel(hidden, *point)) for point in shadow_points],
                          "backdrop_edges": [(point, pixel(shown, *point), pixel(hidden, *point)) for point in edge_points],
                          "shadow_profiles": shadow_profiles}
                (artifacts / f"{tag}.json").write_text(json.dumps(record, indent=2))
                print("OK: rendered quick card " + json.dumps(record), flush=True)
            finally:
                stop(process)
    print("OK: quick terminal edge cells remain whole inside rounded cards; outward shadow extent matches "
          "the large-case profile and clears backdrop edges wherever the measured margin permits")
