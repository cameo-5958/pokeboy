#!/usr/bin/env python3
"""Compile a small JSON manifest into a Pokeboy .gbmod v1 package."""

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

MAGIC = b"GBMOD1\r\n"
NONE = 0xFFFFFFFF
PLACEMENTS = {"fixed": 0, "symbol": 1, "append": 2}
TARGETS = {"section": 0, "patch": 1}
RELOCS = {"abs8": 0, "abs16": 1, "bank8": 2, "rel8": 3,
          "call16": 4, "host16": 5, "bank16": 6}
REFERENCES = {"rom": 0, "module": 1, "host": 2}


class CompileError(ValueError):
    pass


def integer(value, field, low=0, high=0xFFFFFFFF):
    if isinstance(value, bool):
        raise CompileError(f"{field} must be an integer")
    try:
        result = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise CompileError(f"{field} must be an integer") from None
    if result < low or result > high:
        raise CompileError(f"{field} must be in {low}..{high}")
    return result


def enum(value, choices, field):
    try:
        return choices[str(value).lower()]
    except KeyError:
        raise CompileError(f"{field} must be one of: {', '.join(choices)}") from None


def named_indices(items, field):
    result = {}
    for index, item in enumerate(items):
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise CompileError(f"{field}[{index}].name must be a non-empty string")
        if name in result:
            raise CompileError(f"duplicate {field} name: {name}")
        result[name] = index
    return result


def payload(value, base, field):
    if isinstance(value, str):
        text = value.replace("0x", "").replace("$", "")
        text = "".join(text.split())
        try:
            return bytes.fromhex(text)
        except ValueError:
            raise CompileError(f"{field} is not valid hex") from None
    if not isinstance(value, dict) or set(value) != {"file"}:
        raise CompileError(f'{field} must be a hex string or {{"file": "path"}}')
    path = base / str(value["file"])
    try:
        return path.read_bytes()
    except OSError as error:
        raise CompileError(f"cannot read {field} file {path}: {error}") from None


class Strings:
    def __init__(self):
        self.data = bytearray()
        self.offsets = {}

    def add(self, value, field, optional=False):
        if value is None and optional:
            return NONE
        if not isinstance(value, str) or not value or "\0" in value:
            raise CompileError(f"{field} must be a non-empty string")
        if value not in self.offsets:
            self.offsets[value] = len(self.data)
            self.data.extend(value.encode("utf-8") + b"\0")
        return self.offsets[value]


def compile_manifest(document, base):
    if not isinstance(document, dict):
        raise CompileError("manifest root must be an object")
    imports = document.get("imports", [])
    sections = document.get("sections", [])
    patches = document.get("patches", [])
    symbols = document.get("symbols", [])
    relocations = document.get("relocations", [])
    for value, field in ((imports, "imports"), (sections, "sections"),
                         (patches, "patches"), (symbols, "symbols"),
                         (relocations, "relocations")):
        if not isinstance(value, list):
            raise CompileError(f"{field} must be an array")

    section_indices = named_indices(sections, "sections")
    symbol_indices = named_indices(symbols, "symbols")
    import_indices = {}
    for index, name in enumerate(imports):
        if not isinstance(name, str) or not name:
            raise CompileError(f"imports[{index}] must be a non-empty string")
        if name in import_indices:
            raise CompileError(f"duplicate import: {name}")
        import_indices[name] = index

    strings = Strings()
    mod_id = strings.add(document.get("id"), "id")
    display_name = strings.add(document.get("name"), "name", True)
    import_rows = [(strings.add(name, f"imports[{i}]"), 0)
                   for i, name in enumerate(imports)]

    blobs = []
    section_rows = []
    for i, section in enumerate(sections):
        field = f"sections[{i}]"
        data = payload(section.get("data"), base, field + ".data")
        if not 1 <= len(data) <= 0x4000:
            raise CompileError(f"{field}.data must contain 1..16384 bytes")
        placement = enum(section.get("placement", "append"), PLACEMENTS,
                         field + ".placement")
        anchor = strings.add(section.get("symbol"), field + ".symbol", True)
        if placement == 1 and anchor == NONE:
            raise CompileError(f"{field}.symbol is required for symbol placement")
        alignment = integer(section.get("alignment", 1), field + ".alignment", 1, 0x4000)
        if alignment & (alignment - 1):
            raise CompileError(f"{field}.alignment must be a power of two")
        fill = section.get("fill")
        flags = 1 if fill is not None else 0
        section_rows.append([
            strings.add(section["name"], field + ".name"), None, len(data), anchor,
            integer(section.get("addend", 0), field + ".addend", -0x80000000, 0x7fffffff),
            integer(section.get("address", 0), field + ".address", 0, 0xffff),
            integer(section.get("bank", 0), field + ".bank", 0, 0x1ff),
            alignment, placement, flags,
            integer(fill if fill is not None else 0xff, field + ".fill", 0, 0xff), data])
        blobs.append(data)

    patch_rows = []
    for i, patch in enumerate(patches):
        field = f"patches[{i}]"
        data = payload(patch.get("data"), base, field + ".data")
        if not data:
            raise CompileError(f"{field}.data must not be empty")
        expected = (payload(patch["expected"], base, field + ".expected")
                    if "expected" in patch else b"")
        if expected and len(expected) != len(data):
            raise CompileError(f"{field}.expected must be the same size as data")
        kind = 1 if patch.get("symbol") is not None else 0
        patch_rows.append([
            None, len(data), None, len(expected),
            strings.add(patch.get("symbol"), field + ".symbol", True),
            integer(patch.get("addend", 0), field + ".addend", -0x80000000, 0x7fffffff),
            integer(patch.get("address", 0), field + ".address", 0, 0xffff),
            integer(patch.get("bank", 0), field + ".bank", 0, 0x1ff), kind, data, expected])
        blobs.extend((data, expected))

    symbol_rows = []
    for i, symbol in enumerate(symbols):
        field = f"symbols[{i}]"
        section = symbol.get("section")
        if section not in section_indices:
            raise CompileError(f"{field}.section names an unknown section")
        symbol_rows.append((strings.add(symbol["name"], field + ".name"),
                            section_indices[section],
                            integer(symbol.get("offset", 0), field + ".offset"), 0))

    relocation_rows = []
    for i, relocation in enumerate(relocations):
        field = f"relocations[{i}]"
        target_kind = enum(relocation.get("target"), TARGETS, field + ".target")
        target_name = relocation.get("in")
        target_map = section_indices if target_kind == 0 else {
            str(index): index for index in range(len(patches))}
        if target_name not in target_map:
            raise CompileError(f"{field}.in names an unknown target")
        reloc_type = enum(relocation.get("type"), RELOCS, field + ".type")
        reference_kind = enum(relocation.get("reference"), REFERENCES, field + ".reference")
        name = relocation.get("name")
        reference_map = (None if reference_kind == 0 else
                         symbol_indices if reference_kind == 1 else import_indices)
        if reference_kind == 0:
            reference = strings.add(name, field + ".name")
        elif name not in reference_map:
            raise CompileError(f"{field}.name names an unknown reference")
        else:
            reference = reference_map[name]
        if reloc_type == 5 and reference_kind != 2:
            raise CompileError(f"{field}: host16 requires a host reference")
        write_offset = integer(relocation.get("offset", 0), field + ".offset")
        target_blob = (section_rows[target_map[target_name]][-1] if target_kind == 0
                       else patch_rows[target_map[target_name]][-2])
        width = 1 if reloc_type in (0, 2, 3) else 2
        if write_offset + width > len(target_blob):
            raise CompileError(f"{field} writes past its target payload")
        if reloc_type == 4 and (write_offset == 0 or target_blob[write_offset - 1] != 0xcd):
            raise CompileError(f"{field}: call16 must immediately follow opcode cd")
        if reloc_type == 5 and (write_offset == 0 or target_blob[write_offset - 1] != 0xd3):
            raise CompileError(f"{field}: host16 must immediately follow opcode d3")
        relocation_rows.append((target_kind, reloc_type, reference_kind, 0,
                                target_map[target_name],
                                write_offset,
                                reference,
                                integer(relocation.get("addend", 0), field + ".addend",
                                        -0x80000000, 0x7fffffff), 0))

    metadata = document.get("metadata")
    metadata_bytes = (b"" if metadata is None else
                      json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode())
    target_crc = integer(document.get("targetCrc32", 0), "targetCrc32")
    target_size = integer(document.get("targetSize", 0), "targetSize")
    if "targetRom" in document:
        rom = payload({"file": document["targetRom"]}, base, "targetRom")
        calculated = zlib.crc32(rom) & 0xffffffff
        if target_crc and target_crc != calculated:
            raise CompileError("targetCrc32 does not match targetRom")
        if target_size and target_size != len(rom):
            raise CompileError("targetSize does not match targetRom")
        target_crc, target_size = calculated, len(rom)

    counts = (len(import_rows), len(section_rows), len(patch_rows),
              len(symbol_rows), len(relocation_rows))
    sizes = (8, 32, 32, 16, 24)
    offsets = []
    cursor = 96
    for count, size in zip(counts, sizes):
        offsets.append(cursor)
        cursor += count * size
    string_offset, string_size = cursor, len(strings.data)
    cursor += string_size
    metadata_offset, metadata_size = cursor, len(metadata_bytes)
    cursor += metadata_size
    blob_offsets = []
    for blob in blobs:
        blob_offsets.append(cursor)
        cursor += len(blob)

    output = bytearray(cursor)
    output[:8] = MAGIC
    struct.pack_into("<HH" + "I" * 21, output, 8,
                     1, 1, 96, cursor, 0, target_crc, target_size, mod_id, display_name,
                     metadata_offset, metadata_size, string_offset, string_size,
                     offsets[0], counts[0], offsets[1], counts[1], offsets[2], counts[2],
                     offsets[3], counts[3], offsets[4], counts[4])
    at = offsets[0]
    for row in import_rows:
        struct.pack_into("<II", output, at, *row); at += 8
    blob_index = 0
    at = offsets[1]
    for row in section_rows:
        row[1] = blob_offsets[blob_index]; blob_index += 1
        struct.pack_into("<IIIIiHHHBBB3x", output, at, *row[:-1]); at += 32
    at = offsets[2]
    for row in patch_rows:
        row[0] = blob_offsets[blob_index]; blob_index += 1
        row[2] = blob_offsets[blob_index]; blob_index += 1
        struct.pack_into("<IIIIIiHHB3x", output, at,
                         row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8])
        at += 32
    at = offsets[3]
    for row in symbol_rows:
        struct.pack_into("<IIII", output, at, *row); at += 16
    at = offsets[4]
    for row in relocation_rows:
        struct.pack_into("<BBBBIIIiI", output, at, *row); at += 24
    output[string_offset:string_offset + string_size] = strings.data
    output[metadata_offset:metadata_offset + metadata_size] = metadata_bytes
    for offset, blob in zip(blob_offsets, blobs):
        output[offset:offset + len(blob)] = blob
    return bytes(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    args = parser.parse_args(argv)
    output = args.output or args.manifest.with_suffix(".gbmod")
    try:
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        package = compile_manifest(document, args.manifest.parent)
        output.write_bytes(package)
    except (OSError, json.JSONDecodeError, CompileError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"wrote {output} ({len(package)} bytes)")


if __name__ == "__main__":
    main()
