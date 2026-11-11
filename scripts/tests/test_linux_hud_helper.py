#!/usr/bin/env python3
"""Drives the HUD painter Linux ships with real headers and asserts the frames it paints.

The header is `<cols> <rows> <spinner> <pid> <interval> <textcolor> <blockwidth> [frame...]`, written by
agtermCore HudLayout.renderedBody for both apps. The Linux app runs its own copy of the painter under
/bin/sh, which is dash on Debian/Ubuntu and bash elsewhere, so every case runs under each shell present.
The macOS painter is the reference: the parity cases paint the same bodies with both and compare bytes.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / 'agterm-linux/Sources/AgtermLinux/Resources/hud/hud.sh'
MACOS = ROOT / 'agterm/Resources/hud/hud.sh'
ESC = '\x1b'


def shells():
    found, seen = [], set()
    for name in ('/bin/sh', 'bash', 'dash'):
        path = shutil.which(name)
        if path and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            found.append(path)
    return found


def header(cols, rows, spinner=0, owner=0, interval='0.05', color='-', blockw=0, frames=()):
    return ' '.join([str(cols), str(rows), str(spinner), str(owner), interval, color, str(blockw), *frames]) + '\n'


class Painter:
    """One painter process writing into a file, so the stream can be read at any point."""

    def __init__(self, test, shell, script, body):
        self.dir = Path(tempfile.mkdtemp(prefix='agterm-hud-'))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.body = self.dir / 'body'
        self.out = self.dir / 'out'
        self.write(body)
        # no LANG/LC_*: the script's own LC_CTYPE must be what makes ${#line} count characters
        env = {'AGTERM_HUD_FILE': str(self.body), 'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
        with open(self.out, 'wb') as out:
            self.proc = subprocess.Popen([shell, str(script)], env=env, stdout=out, stderr=subprocess.DEVNULL)
        test.addCleanup(self.stop)

    def write(self, text):
        tmp = self.dir / 'body.tmp'
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, self.body)

    @property
    def painted(self):
        return self.out.read_bytes().decode('utf-8', 'replace')

    def wait(self, check, timeout=4.0):
        deadline = time.monotonic() + timeout
        text = self.painted
        while not check(text) and time.monotonic() < deadline:
            time.sleep(0.02)
            text = self.painted
        return text

    def paints(self, *needles):
        text = self.wait(lambda t: all(n in t for n in needles))
        return [n for n in needles if n not in text]

    def exited(self, timeout=4.0):
        try:
            self.proc.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class LinuxHudHelperTests(unittest.TestCase):
    def shells(self):
        found = shells()
        self.assertTrue(found, 'no POSIX shell to run the painter with')
        return found

    def test_the_painter_linux_ships_exists(self):
        self.assertTrue(LINUX.is_file(), f'{LINUX} moved; Package.swift copies Resources/hud')

    def test_spinner_cycles_only_through_the_header_frames(self):
        # the blockwidth field (0 here) is not a frame: a stray `0 hello` is the defect this pins
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 5, 1, frames=('A', 'B', 'C')) + 'hello\n')
                text = p.wait(lambda t: t.count(' hello') >= 7)
                glyphs = [chunk[-1] for chunk in text.split(' hello')[:-1]]
                self.assertGreaterEqual(len(glyphs), 7, text)
                self.assertEqual(glyphs[:6], ['A', 'B', 'C', 'A', 'B', 'C'], glyphs)
                self.assertLessEqual(set(glyphs), {'A', 'B', 'C'}, glyphs)

    def test_spinner_glyph_keeps_the_message_centered(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, 1, frames=('|', '/')) + 'busy\n')
                # 40 columns less 4 for "busy" less 2 for the glyph leaves 17
                self.assertEqual(p.paints(f'{ESC}[17C| busy', f'{ESC}[17C/ busy'), [])

    def test_places_the_message_in_the_box_the_header_gave(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(41, 9) + 'abc\n')
                e = ESC
                self.assertEqual(p.paints(f'{e}[H{e}[J{e}[E{e}[E{e}[E{e}[E{e}[19Cabc'), [])

    def test_dims_everything_after_the_empty_line_in_plain_mode(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 9) + 'working\n\nthis may take a while\n')
                self.assertEqual(p.paints(f'{ESC}[2mthis may take a while'), [])
                self.assertNotIn(f'{ESC}[2mworking', p.painted)

    def test_a_markdown_body_is_painted_verbatim_at_one_shared_offset(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, blockw=5) + f'  • a\n{ESC}[1mb{ESC}[22m\n')
                self.assertEqual(p.paints(f'{ESC}[17C  • a', f'{ESC}[17C{ESC}[1mb{ESC}[22m'), [])

    def test_a_markdown_blank_row_is_spacing_not_the_detail_separator(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, blockw=1) + 'a\n\nb\n')
                self.assertEqual(p.paints(f'a{ESC}[E{ESC}[E{ESC}[19Cb'), [])
                self.assertNotIn(f'{ESC}[2m', p.painted)

    def test_a_spinning_markdown_body_puts_the_glyph_in_the_first_rows_gutter(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, 1, blockw=9, frames=('|', '/')) + 'a\n  b\n')
                self.assertEqual(p.paints(f'{ESC}[15C| a{ESC}[E{ESC}[15C  b', f'{ESC}[15C/ a'), [])

    def test_one_painter_switches_between_plain_and_markdown(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7) + 'plain\n')
                self.assertEqual(p.paints('plain'), [])
                p.write(header(40, 7, blockw=10) + '  md\n')
                self.assertEqual(p.paints(f'{ESC}[15C  md'), [])
                p.write(header(40, 7) + 'back\n')
                self.assertEqual(p.paints(f'{ESC}[18Cback'), [])
                self.assertIsNone(p.proc.poll())

    def test_centers_a_non_ascii_message_by_characters_not_bytes(self):
        # 41 columns less 11 characters leaves 15; counting the 13 bytes would leave 14. dash counts bytes in
        # any locale, which is why the painter moves itself onto bash when it starts under another sh.
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(41, 5) + 'a message …\n')
                self.assertEqual(p.paints(f'{ESC}[15Ca message …'), [], p.painted[:200])

    def test_text_color_sits_between_the_erase_and_the_content(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, color='38;2;0;0;255') + 'busy\n')
                self.assertEqual(p.paints(f'{ESC}[J{ESC}[38;2;0;0;255m'), [])

    def test_a_malformed_text_color_paints_no_escape(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, color='1;J') + 'busy\n')
                p.wait(lambda t: 'busy' in t)
                self.assertNotIn(f'{ESC}[1;Jm', p.painted)

    def test_exits_and_restores_the_cursor_when_the_body_disappears(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, 1, frames=('|', '/')) + 'bye\n')
                self.assertEqual(p.paints('bye'), [])
                self.assertTrue(p.painted.startswith(f'{ESC}[?25l'), repr(p.painted[:20]))
                p.body.unlink()
                self.assertTrue(p.exited())
                self.assertIn(f'{ESC}[?25h', p.painted)

    def test_exits_when_the_owning_process_is_gone(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                owner = subprocess.Popen(['sleep', '30'])
                self.addCleanup(owner.kill)
                p = Painter(self, shell, LINUX, header(40, 7, 1, owner=owner.pid, frames=('|', '/')) + 'bye\n')
                self.assertEqual(p.paints('bye'), [])
                self.assertIsNone(p.proc.poll())
                owner.kill()
                owner.wait()  # reaped: kill -0 succeeds on a zombie
                self.assertTrue(p.exited())

    def test_stops_writing_once_a_spinnerless_frame_is_settled(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                p = Painter(self, shell, LINUX, header(40, 7, interval='0.1') + 'settled\n')
                p.wait(lambda t: 'settled' in t)
                before = p.painted
                time.sleep(0.4)
                self.assertEqual(p.painted, before)

    def test_exits_quietly_without_a_body_path(self):
        for shell in self.shells():
            with self.subTest(shell=shell):
                done = subprocess.run([shell, str(LINUX)], env={}, capture_output=True, timeout=5)
                self.assertEqual((done.returncode, done.stdout), (0, b''))


class HudPainterParityTests(unittest.TestCase):
    """The Linux painter paints what the macOS reference paints. ASCII bodies only: the reference names the
    macOS locale (LC_CTYPE=UTF-8), which glibc does not have, so it measures bytes here."""

    CASES = {
        'plain': header(41, 9) + 'abc\n',
        'detail': header(40, 9) + 'working\n\nthis may take a while\n',
        'oversized': header(10, 3) + 'a message wider than its box\n',
        'markdown': header(40, 7, blockw=5) + f'  - a\n{ESC}[1mb{ESC}[22m\n',
        'markdown-blank': header(40, 7, blockw=1) + 'a\n\nb\n',
        'color': header(40, 7, color='38;2;126;192;126') + 'busy\n',
        'malformed-header': 'not a header\nvisible\n',
        'malformed-blockwidth': '40 7 0 0 0.5 - x\nhey\n',
    }

    def settled(self, script, body):
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('the reference painter needs bash on Linux')
        p = Painter(self, bash, script, body)
        p.wait(lambda t: t.endswith(f'{ESC}[0m') and t.count(f'{ESC}[H') >= 1)
        time.sleep(0.15)
        return p.painted

    def test_static_bodies_paint_the_same_bytes_as_the_macos_reference(self):
        for name, body in self.CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.settled(LINUX, body), self.settled(MACOS, body))

    def test_spinning_bodies_paint_the_same_first_frame(self):
        body = header(40, 7, 1, interval='5', blockw=9, frames=('|', '/')) + 'a\n  b\n'
        self.assertEqual(self.settled(LINUX, body), self.settled(MACOS, body))


if __name__ == '__main__':
    unittest.main()
