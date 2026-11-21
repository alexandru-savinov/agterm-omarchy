#!/usr/bin/env python3
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("runpath", Path(__file__).resolve().parents[1] / "linux-runpath.py")
runpath = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runpath)
OLD = "/usr/lib/swift/linux:$ORIGIN:/__w/agterm-omarchy/agterm-omarchy/agterm-linux/vendor/ghostty/lib"


def elf(search=OLD, tag=runpath.DT_RUNPATH, needed_offset=None, symbol_offset=None, sections=True):
    strings = b"\0libghostty.so\0" + search.encode() + b"\0swift_retain\0"
    search_offset = strings.index(search.encode())
    needed = 1 if needed_offset is None else needed_offset
    symbol = strings.index(b"swift_retain") if symbol_offset is None else symbol_offset
    dynstr, dynsym, dynamic, headers = 0x100, 0x200, 0x300, 0x400
    dynamic_entries = [(runpath.DT_NEEDED, needed), (tag, search_offset), (runpath.DT_STRTAB, dynstr),
                       (runpath.DT_STRSZ, len(strings)), (0, 0)]
    data = bytearray(0x400 + (3 * 64 if sections else 0))
    struct.pack_into("<4sBB", data, 0, b"\x7fELF", 2, 1)
    struct.pack_into("<QQ", data, 0x20, 0x40, headers if sections else 0)
    struct.pack_into("<HHHH", data, 0x36, 56, 2, 64, 3 if sections else 0)
    struct.pack_into("<IIQQQQQQ", data, 0x40, 1, 5, 0, 0, 0, len(data), len(data), 0x1000)
    struct.pack_into("<IIQQQQQQ", data, 0x78, 2, 6, dynamic, dynamic, dynamic, 16 * len(dynamic_entries), 0, 8)
    data[dynstr:dynstr + len(strings)] = strings
    struct.pack_into("<I", data, dynsym + 24, symbol)
    for index, entry in enumerate(dynamic_entries):
        struct.pack_into("<qQ", data, dynamic + 16 * index, *entry)
    if sections:
        struct.pack_into("<IIQQQQIIQQ", data, headers + 64, 0, 3, 2, dynstr, dynstr, len(strings), 0, 0, 1, 0)
        struct.pack_into("<IIQQQQIIQQ", data, headers + 128, 0, runpath.SHT_DYNSYM, 2, dynsym, dynsym, 48, 1, 1, 8, 24)
    return bytes(data), search_offset


class RunpathTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def write(self, data):
        path = self.directory / "agterm-linux.bin"
        path.write_bytes(data)
        return path

    def test_rewrites_runpath_and_keeps_every_other_string(self):
        path = self.write(elf()[0])
        self.assertEqual(runpath.set_runpath(path, "$ORIGIN/../lib"), OLD)
        image = runpath.Elf(path.read_bytes())
        self.assertEqual(image.search_paths(), {"RUNPATH": "$ORIGIN/../lib"})
        self.assertEqual(image.string(1), "libghostty.so")
        self.assertEqual(image.string(image.string_references()[-1]), "swift_retain")
        self.assertEqual(len(path.read_bytes()), len(elf()[0]))

    def test_keeps_a_legacy_rpath_tag(self):
        path = self.write(elf(tag=runpath.DT_RPATH)[0])
        runpath.set_runpath(path, "$ORIGIN/../lib")
        self.assertEqual(runpath.Elf(path.read_bytes()).search_paths(), {"RPATH": "$ORIGIN/../lib"})

    def test_refuses_a_longer_value_without_writing(self):
        original = elf("$ORIGIN")[0]
        path = self.write(original)
        with self.assertRaisesRegex(ValueError, "longer"):
            runpath.set_runpath(path, "$ORIGIN/../lib")
        self.assertEqual(path.read_bytes(), original)

    def test_refuses_strings_sharing_the_old_bytes(self):
        offset = elf()[1]
        tail = offset + OLD.index("lib")
        for fixture in (elf(needed_offset=tail)[0], elf(symbol_offset=tail)[0]):
            with self.subTest():
                path = self.write(fixture)
                with self.assertRaisesRegex(ValueError, "shares"):
                    runpath.set_runpath(path, "$ORIGIN/../lib")
                self.assertEqual(path.read_bytes(), fixture)

    def test_refuses_without_section_headers(self):
        path = self.write(elf(sections=False)[0])
        with self.assertRaisesRegex(ValueError, "section headers"):
            runpath.set_runpath(path, "$ORIGIN/../lib")

    def test_check_accepts_only_origin_relative_entries(self):
        self.assertEqual(runpath.check([self.write(elf("$ORIGIN:$ORIGIN/../lib")[0])]), [])
        self.assertEqual(len(runpath.check([self.write(elf()[0])])), 1)
        script = self.directory / "agterm-linux"
        script.write_text("#!/bin/sh\n")
        self.assertEqual(runpath.check([script]), [])

    def test_real_linker_output_loads_its_library_from_the_new_runpath(self):
        compiler = shutil.which("cc")
        if compiler is None or shutil.which("readelf") is None:
            if os.environ.get("CI"):
                self.fail("CI must provide cc and readelf for the real ELF case")
            self.skipTest("needs cc and readelf")
        build = self.directory / "build"
        (build / "lib").mkdir(parents=True)
        (build / "bin").mkdir()
        (build / "lib/libprobe.c").write_text("int probe(void) { return 7; }\n")
        (build / "bin/main.c").write_text("int probe(void);\nint main(void) { return probe() == 7 ? 0 : 1; }\n")
        subprocess.run([compiler, "-shared", "-fPIC", "-o", build / "lib/libprobe.so", build / "lib/libprobe.c"], check=True)
        long_path = f"{build}/lib:/__w/agterm-omarchy/agterm-omarchy/agterm-linux/vendor/ghostty/lib"
        subprocess.run([compiler, "-o", build / "bin/main", build / "bin/main.c", f"-L{build}/lib", "-lprobe",
                        f"-Wl,--enable-new-dtags,-rpath,{long_path}"], check=True)
        stage = self.directory / "stage"
        shutil.copytree(build, stage)
        shutil.rmtree(build)
        runpath.set_runpath(stage / "bin/main", "$ORIGIN/../lib")
        dynamic = subprocess.run(["readelf", "-d", stage / "bin/main"], check=True, capture_output=True, text=True).stdout
        self.assertRegex(dynamic, r"\(RUNPATH\)\s+Library runpath: \[\$ORIGIN/\.\./lib\]")
        self.assertNotIn("/__w/", dynamic)
        environment = {key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"}
        subprocess.run([stage / "bin/main"], check=True, env=environment)


if __name__ == "__main__":
    unittest.main()
