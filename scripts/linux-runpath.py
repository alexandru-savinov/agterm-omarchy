#!/usr/bin/env python3
"""Read, check or shorten the RUNPATH of 64-bit little-endian ELF files without patchelf.

`set` overwrites the existing RUNPATH string inside .dynstr, so the new value must not be longer.
It refuses when any other dynamic string reference starts inside the old value: linkers may share a
string's tail, and overwriting it would silently rename a needed library, symbol or version.
"""
import argparse
from pathlib import Path
import struct
import sys

DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 5, 10, 14, 15, 29
STRING_TAGS = {DT_NEEDED, DT_SONAME, DT_RPATH, DT_RUNPATH, 0x7FFFFFFD, 0x7FFFFFFE, 0x7FFFFFFF}
SHT_DYNSYM, SHT_VERDEF, SHT_VERNEED = 11, 0x6FFFFFFD, 0x6FFFFFFE


def require(condition, message):
    if not condition:
        raise ValueError(message)


def is_elf(data):
    return data[:4] == b"\x7fELF"


class Elf:
    def __init__(self, data):
        require(is_elf(data) and data[4] == 2 and data[5] == 1, "not a 64-bit little-endian ELF file")
        self.data = data
        phoff, shoff = struct.unpack_from("<QQ", data, 0x20)
        phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", data, 0x36)
        self.loads, dynamic = [], None
        for index in range(phnum):
            kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phentsize)
            if kind == 1:
                self.loads.append((vaddr, offset, filesz))
            elif kind == 2:
                dynamic = (offset, filesz)
        self.sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize)
                         for index in range(shnum)] if shoff else []
        self.entries = []
        if dynamic is None:
            return
        for offset in range(dynamic[0], dynamic[0] + dynamic[1], 16):
            tag, value = struct.unpack_from("<qQ", data, offset)
            if tag == 0:
                break
            self.entries.append((tag, value))
        strtab = [value for tag, value in self.entries if tag == DT_STRTAB]
        size = [value for tag, value in self.entries if tag == DT_STRSZ]
        require(len(strtab) == 1 and len(size) == 1, "dynamic section lacks a unique string table")
        self.strtab = self.file_offset(strtab[0])
        self.strsz = size[0]
        require(self.strtab + self.strsz <= len(data), "string table outside the file")

    def file_offset(self, address):
        for vaddr, offset, filesz in self.loads:
            if vaddr <= address < vaddr + filesz:
                return offset + address - vaddr
        raise ValueError("address outside every loaded segment")

    def string(self, offset):
        require(offset < self.strsz, "string offset outside the string table")
        start = self.strtab + offset
        end = self.data.index(b"\0", start)
        require(end < self.strtab + self.strsz, "unterminated dynamic string")
        return self.data[start:end].decode()

    def search_paths(self):
        """{'RPATH'|'RUNPATH': value} for each search-path entry present."""
        return {("RPATH" if tag == DT_RPATH else "RUNPATH"): self.string(value)
                for tag, value in self.entries if tag in (DT_RPATH, DT_RUNPATH)}

    def string_references(self):
        """Every .dynstr offset the dynamic loader or symbol versioning reads."""
        references = [value for tag, value in self.entries if tag in STRING_TAGS]
        require(self.sections, "section headers are required to find every dynamic string reference")
        for _, kind, _, _, offset, size, link, info, _, entsize in self.sections:
            if link >= len(self.sections) or self.sections[link][4] != self.strtab:
                continue
            if kind == SHT_DYNSYM:
                require(entsize == 24, "unexpected dynamic symbol size")
                references += [struct.unpack_from("<I", self.data, offset + index)[0] for index in range(0, size, 24)]
            elif kind == SHT_VERNEED:
                entry = offset
                for _ in range(info):
                    _, count, name, aux, following = struct.unpack_from("<HHIII", self.data, entry)
                    references.append(name)
                    position = entry + aux
                    for _ in range(count):
                        _, _, _, name, step = struct.unpack_from("<IHHII", self.data, position)
                        references.append(name)
                        position += step
                    entry += following
            elif kind == SHT_VERDEF:
                entry = offset
                for _ in range(info):
                    _, _, _, count, _, aux, following = struct.unpack_from("<HHHHIII", self.data, entry)
                    position = entry + aux
                    for _ in range(count):
                        name, step = struct.unpack_from("<II", self.data, position)
                        references.append(name)
                        position += step
                    entry += following
        return references


def set_runpath(path, value):
    data = bytearray(Path(path).read_bytes())
    elf = Elf(bytes(data))
    targets = [(tag, offset) for tag, offset in elf.entries if tag in (DT_RPATH, DT_RUNPATH)]
    require(len(targets) == 1, f"{path}: expected exactly one RPATH or RUNPATH entry, found {len(targets)}")
    offset = targets[0][1]
    old = elf.string(offset)
    encoded = value.encode()
    require(b"\0" not in encoded and len(encoded) <= len(old.encode()),
            f"{path}: new search path is longer than the existing {len(old.encode())} bytes")
    end = offset + len(old.encode())
    shared = [item for item in elf.string_references() if offset < item < end]
    require(not shared, f"{path}: another dynamic string shares the old search path's bytes")
    start = elf.strtab + offset
    data[start:start + len(old.encode())] = encoded + b"\0" * (len(old.encode()) - len(encoded))
    Path(path).write_bytes(data)
    require(Elf(bytes(data)).search_paths() == {("RPATH" if targets[0][0] == DT_RPATH else "RUNPATH"): value},
            f"{path}: search path did not read back")
    return old


def relative_search_path(value):
    return all(part.startswith(("$ORIGIN", "${ORIGIN}")) for part in value.split(":"))


def check(paths):
    failures = []
    for path in paths:
        if not Path(path).is_file():
            continue
        data = Path(path).read_bytes()
        if not is_elf(data):
            continue
        entries = Elf(data).search_paths()
        print(f"{path}: {entries or 'no search path'}")
        failures += [f"{path}: {kind}={value}" for kind, value in entries.items() if not relative_search_path(value)]
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setter = commands.add_parser("set", help="overwrite each file's single RPATH/RUNPATH with VALUE")
    setter.add_argument("value")
    setter.add_argument("files", nargs="+")
    checker = commands.add_parser("check", help="fail unless every ELF search path entry is $ORIGIN-relative")
    checker.add_argument("files", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "set":
            for path in args.files:
                print(f"{path}: {set_runpath(path, args.value)} -> {args.value}")
        else:
            failures = check(args.files)
            if failures:
                raise ValueError("absolute search paths:\n" + "\n".join(failures))
    except (OSError, ValueError, struct.error) as error:
        sys.exit(f"linux-runpath: {error}")


if __name__ == "__main__":
    main()
