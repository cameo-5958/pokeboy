#!/usr/bin/env python3
"""Build the custom, CPU-executed boot ROM from the first five video seconds."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


FPS = 6
FRAME_COUNT = 5 * FPS
WIDTH, HEIGHT = 160, 144
TILE_DATA_BYTES = 256 * 16
TILE_MAP_BYTES = 32 * 32
FRAME_BYTES = TILE_DATA_BYTES + TILE_MAP_BYTES
FIXED_SIZE = BANK_SIZE = 0x4000
DESCRIPTORS = 0x0800
MAIN = 0x0200


class Assembler:
    def __init__(self, origin: int):
        self.origin = origin
        self.data = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    @property
    def pc(self) -> int:
        return self.origin + len(self.data)

    def emit(self, *values: int) -> None:
        self.data.extend(v & 0xFF for v in values)

    def label(self, name: str) -> None:
        self.labels[name] = self.pc

    def jr(self, opcode: int, label: str) -> None:
        self.emit(opcode, 0)
        self.fixups.append((len(self.data) - 1, label))

    def finish(self) -> bytes:
        for operand, label in self.fixups:
            target = self.labels[label]
            source_after = self.origin + operand + 1
            delta = target - source_after
            if not -128 <= delta <= 127:
                raise ValueError(f"JR to {label} is out of range")
            self.data[operand] = delta & 0xFF
        return bytes(self.data)


def extract_frames(ffmpeg: Path, video: Path) -> list[bytes]:
    command = [
        str(ffmpeg), "-v", "error", "-ss", "0", "-t", "5", "-i", str(video),
        "-vf",
        "fps=6,scale=160:144:force_original_aspect_ratio=increase,"
        "crop=160:144,format=gray",
        "-frames:v", str(FRAME_COUNT), "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.check_output(command)
    stride = WIDTH * HEIGHT
    if len(raw) != FRAME_COUNT * stride:
        raise RuntimeError(f"expected {FRAME_COUNT} frames, got {len(raw) / stride:.2f}")
    return [raw[i * stride : (i + 1) * stride] for i in range(FRAME_COUNT)]


def load_gray_frames(path: Path) -> list[bytes]:
    raw = path.read_bytes()
    stride = WIDTH * HEIGHT
    if len(raw) != FRAME_COUNT * stride:
        raise RuntimeError(f"expected {FRAME_COUNT} Gray8 frames, got {len(raw) / stride:.2f}")
    return [raw[i * stride : (i + 1) * stride] for i in range(FRAME_COUNT)]


def quantize(gray: bytes) -> list[int]:
    # Direct four-level quantization avoids exceeding the DMG's 256-tile limit.
    return [3 - min(3, (value + 42) // 85) for value in gray]


def encode_frame(pixels: list[int]) -> bytes:
    tile_data = bytearray()
    tile_map = bytearray(TILE_MAP_BYTES)
    known: dict[bytes, int] = {}
    for tile_y in range(HEIGHT // 8):
        for tile_x in range(WIDTH // 8):
            encoded = bytearray()
            for row in range(8):
                low = high = 0
                for column in range(8):
                    color = pixels[(tile_y * 8 + row) * WIDTH + tile_x * 8 + column]
                    bit = 7 - column
                    low |= (color & 1) << bit
                    high |= ((color >> 1) & 1) << bit
                encoded.extend((low, high))
            tile = bytes(encoded)
            if tile not in known:
                if len(known) == 256:
                    raise RuntimeError("frame needs more than 256 unique Game Boy tiles")
                known[tile] = len(known)
                tile_data.extend(tile)
            tile_map[tile_y * 32 + tile_x] = known[tile]
    tile_data.extend(bytes(TILE_DATA_BYTES - len(tile_data)))
    return bytes(tile_data + tile_map)


def emit_copy_loop(asm: Assembler, name: str) -> None:
    asm.label(name)
    asm.emit(0x1A, 0x13, 0x22, 0x0B, 0x78, 0xB1)  # LD A,(DE); INC DE; LD (HL+),A; DEC BC; B|C
    asm.jr(0x20, name)


def make_program() -> bytes:
    asm = Assembler(MAIN)
    asm.emit(0xF3, 0x31, 0xFE, 0xFF)               # DI; LD SP,$FFFE
    asm.emit(0xAF, 0xE0, 0x40, 0xE0, 0x42, 0xE0, 0x43)  # LCD off; SCY=SCX=0

    asm.emit(0x3E, 0xE4, 0xE0, 0x47)               # identity four-shade palette
    asm.emit(0x3E, 0x91, 0xE0, 0x40)               # LCD + background on
    asm.emit(0x3E, FRAME_COUNT, 0xE0, 0x80)        # frame counter in HRAM
    asm.emit(0x21, DESCRIPTORS & 0xFF, DESCRIPTORS >> 8)

    asm.label("frame")
    asm.emit(0x2A, 0xE0, 0x51)                     # select boot-data bank
    asm.emit(0x2A, 0x5F, 0x2A, 0x57)               # descriptor source -> DE
    asm.emit(0xE5, 0x21, 0x10, 0x80)               # save descriptor; HL = tile 1
    asm.emit(0x01, TILE_DATA_BYTES & 0xFF, TILE_DATA_BYTES >> 8)
    emit_copy_loop(asm, "copy_tiles")
    asm.emit(0x21, 0x00, 0x98, 0x01, TILE_MAP_BYTES & 0xFF, TILE_MAP_BYTES >> 8)
    emit_copy_loop(asm, "copy_map")
    asm.emit(0xE1)                                 # next descriptor

    # Copy + delay totals 699,056 T-cycles per frame: effectively 6 fps on DMG.
    asm.emit(0x01, 0x5C, 0x3C)                     # BC = 15,452
    asm.label("delay")
    asm.emit(0x0B, 0x78, 0xB1)
    asm.jr(0x20, "delay")
    asm.emit(0xF0, 0x80, 0x3D, 0xE0, 0x80)
    asm.jr(0x20, "frame")

    # Match the documented DMG post-boot register state before unmapping.
    io = (
        (0x00, 0xCF), (0x02, 0x7E), (0x04, 0xAB), (0x07, 0xF8), (0x0F, 0xE1),
        (0x10, 0x80), (0x11, 0xBF), (0x12, 0xF3), (0x13, 0xFF), (0x14, 0xBF),
        (0x16, 0x3F), (0x18, 0xFF), (0x19, 0xBF), (0x1A, 0x7F), (0x1B, 0xFF),
        (0x1C, 0x9F), (0x1D, 0xFF), (0x1E, 0xBF), (0x20, 0xFF), (0x23, 0xBF),
        (0x24, 0x77), (0x25, 0xF3), (0x26, 0xF1), (0x40, 0x91), (0x41, 0x85),
        (0x46, 0xFF), (0x47, 0xFC),
    )
    for address, value in io:
        asm.emit(0x3E, value, 0xE0, address)
    asm.emit(0x01, 0x13, 0x00, 0x11, 0xD8, 0x00)   # BC, DE
    asm.emit(0x21, 0xB0, 0x01, 0xE5, 0xF1)         # AF=$01B0 via stack
    asm.emit(0x21, 0x4D, 0x01, 0x31, 0xFE, 0xFF)   # HL, SP
    asm.emit(0x3E, 0x01, 0xC3, 0xFE, 0x00)         # A=1; JP $00FE
    return asm.finish()


def build_rom(frames: list[bytes]) -> bytes:
    fixed = bytearray(FIXED_SIZE)
    fixed[0:3] = bytes((0xC3, MAIN & 0xFF, MAIN >> 8))
    fixed[0xFE:0x100] = bytes((0xE0, 0x50))         # unmap; next fetch is cart $0100
    program = make_program()
    fixed[MAIN : MAIN + len(program)] = program

    frames_per_bank = BANK_SIZE // FRAME_BYTES
    banks = [bytearray(BANK_SIZE) for _ in range((len(frames) + frames_per_bank - 1) // frames_per_bank)]
    descriptors = bytearray()
    for index, frame in enumerate(frames):
        bank = index // frames_per_bank
        offset = (index % frames_per_bank) * FRAME_BYTES
        banks[bank][offset : offset + FRAME_BYTES] = frame
        source = 0x4000 + offset
        descriptors.extend((bank, source & 0xFF, source >> 8))
    fixed[DESCRIPTORS : DESCRIPTORS + len(descriptors)] = descriptors
    return bytes(fixed + b"".join(banks))


def write_header(rom: bytes, output: Path) -> None:
    lines = [
        "#pragma once", "#include <cstddef>", "#include <cstdint>", "",
        "// Generated by tools/make_boot_rom.py from exactly 0:00-0:05 at 6 fps.",
        f"inline constexpr size_t CUSTOM_BOOT_FIXED_SIZE = 0x{FIXED_SIZE:04X};",
        f"inline constexpr size_t CUSTOM_BOOT_BANK_SIZE = 0x{BANK_SIZE:04X};",
        "inline constexpr uint8_t CUSTOM_BOOT_ROM[] = {",
    ]
    for start in range(0, len(rom), 16):
        lines.append("    " + ", ".join(f"0x{v:02X}" for v in rom[start:start + 16]) + ",")
    lines.extend(("};", "inline constexpr size_t CUSTOM_BOOT_ROM_SIZE = sizeof(CUSTOM_BOOT_ROM);", ""))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", nargs="?", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--raw-gray", type=Path, help="30 concatenated 160x144 Gray8 frames")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.raw_gray:
        frames = load_gray_frames(args.raw_gray)
    elif args.video and args.ffmpeg:
        frames = extract_frames(args.ffmpeg, args.video)
    else:
        parser.error("provide --raw-gray, or provide video and --ffmpeg")
    encoded = [encode_frame(quantize(frame)) for frame in frames]
    write_header(build_rom(encoded), args.output)


if __name__ == "__main__":
    main()
