#!/usr/bin/env python3
"""Build the custom, CPU-executed boot ROM.

Two content modes share one data-driven playback engine:

  --logo pokeboy_logo.txt   POKEBOY logo with palette fade-in, APU chime, and
                            a glint sweep - 60 fps, ~2.7 s (the default boot).
  video / --raw-gray        Legacy slideshow: the first five video seconds at
                            6 fps.

The engine is a tiny copy-list interpreter: each animation frame is a list of
(destination, bytes) copies plus a cycle-counted delay. Because the DMG maps
everything into one address space, tile data, tilemap rows, palette (BGP)
fades, LCD control, and the chime's APU register writes are all just copy
entries - the program itself never changes between modes. Only tiles that
actually change are rewritten, so a frame costs microseconds instead of the
full 5 KB redraw the old slideshow did (the source of its lag).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


WIDTH, HEIGHT = 160, 144
CELLS_X, CELLS_Y = WIDTH // 8, HEIGHT // 8
TILE_DATA_BYTES = 256 * 16
TILE_MAP_BYTES = 32 * 32
FIXED_SIZE = BANK_SIZE = 0x4000
DESCRIPTORS = 0x0800
MAIN = 0x0200
DMG_FRAME_CYCLES = 70224

VRAM_TILES = 0x8000
VRAM_MAP = 0x9800

# Logo timeline (60 fps display frames).
LOGO_TOTAL_FRAMES = 160
FADE_STEPS = ((6, 0x40), (10, 0x80), (14, 0xC0), (18, 0xE4))
CHIME1_FRAME = 20
CHIME2_FRAME = 25
GLINT_START, GLINT_END = 40, 100
GLINT_BAND = 5
GLINT_SHADE = 1

# Legacy video mode.
VIDEO_FPS = 6
VIDEO_FRAME_COUNT = 5 * VIDEO_FPS

# The DMG boot chime: channel 1 square wave at ~1048.6 Hz, re-pitched to
# ~2080.5 Hz a few frames later and left to ring out on its envelope.
CHIME1_WRITES = (
    (0xFF26, 0x80),  # NR52 master on
    (0xFF25, 0xF3),  # NR51 routing
    (0xFF24, 0x77),  # NR50 volume
    (0xFF11, 0x80),  # NR11 50% duty
    (0xFF12, 0xF3),  # NR12 envelope: start F, decay
    (0xFF13, 0x83),  # NR13 freq low  ($783 = 1048.6 Hz)
    (0xFF14, 0x87),  # NR14 freq high + trigger
)
CHIME2_WRITES = (
    (0xFF13, 0xC1),  # NR13 freq low  ($7C1 = 2080.5 Hz)
    (0xFF14, 0x87),  # NR14 freq high + trigger
)


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


# ---------------------------------------------------------------------------
# Frame model: an animation frame is a list of copies plus a display duration.

class Frame:
    def __init__(self, step: int = 1):
        self.copies: list[tuple[int, bytes]] = []  # (destination, data)
        self.step = step                           # display frames to occupy

    def copy(self, dst: int, data: bytes) -> None:
        if data:
            self.copies.append((dst, bytes(data)))

    def io(self, writes) -> None:
        for address, value in writes:
            self.copy(address, bytes((value,)))


# ---------------------------------------------------------------------------
# Logo mode

def load_logo(path: Path) -> list[str]:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise RuntimeError("logo rows must be non-empty and equal width")
    if len(rows) > 32 or len(rows[0]) > WIDTH:
        raise RuntimeError("logo must fit the screen")
    if any(ch not in ".#" for row in rows for ch in row):
        raise RuntimeError("logo may only contain '.' and '#'")
    return rows


def render_logo_frame(rows: list[str], x0: int, y0: int, glint_pos: float | None) -> list[int]:
    pixels = [0] * (WIDTH * HEIGHT)
    for ly, row in enumerate(rows):
        for lx, ch in enumerate(row):
            if ch != "#":
                continue
            shade = 3
            if glint_pos is not None and abs(lx + ly - glint_pos) <= GLINT_BAND:
                shade = GLINT_SHADE
            pixels[(y0 + ly) * WIDTH + x0 + lx] = shade
    return pixels


def encode_cell(pixels: list[int], cx: int, cy: int) -> bytes:
    encoded = bytearray()
    for row in range(8):
        low = high = 0
        for column in range(8):
            color = pixels[(cy * 8 + row) * WIDTH + cx * 8 + column]
            bit = 7 - column
            low |= (color & 1) << bit
            high |= ((color >> 1) & 1) << bit
        encoded.extend((low, high))
    return bytes(encoded)


def merge_runs(changes: list[tuple[int, bytes]]) -> list[tuple[int, bytes]]:
    """Coalesce copies whose destinations are contiguous."""
    merged: list[tuple[int, bytearray]] = []
    for dst, data in sorted(changes):
        if merged and merged[-1][0] + len(merged[-1][1]) == dst:
            merged[-1][1].extend(data)
        else:
            merged.append((dst, bytearray(data)))
    return [(dst, bytes(data)) for dst, data in merged]


def logo_frames(rows: list[str]) -> list[Frame]:
    width, height = len(rows[0]), len(rows)
    x0, y0 = (WIDTH - width) // 2, (HEIGHT - height) // 2
    # Active cells: every 8x8 cell the logo ever touches, row-major. Each gets
    # its own tile index (1..N, tile 0 stays the blank background) so a glint
    # step only rewrites the 16 bytes of each cell it crosses.
    cx0, cx1 = x0 // 8, (x0 + width - 1) // 8
    cy0, cy1 = y0 // 8, (y0 + height - 1) // 8
    active = [(cx, cy) for cy in range(cy0, cy1 + 1) for cx in range(cx0, cx1 + 1)]
    if len(active) > 255:
        raise RuntimeError("logo spans more than 255 tiles")
    index = {cell: i + 1 for i, cell in enumerate(active)}

    def cell_bytes(pixels: list[int]) -> dict[tuple[int, int], bytes]:
        return {cell: encode_cell(pixels, *cell) for cell in active}

    def glint_pos(frame: int) -> float | None:
        if not GLINT_START <= frame < GLINT_END:
            return None
        span = width + height + 2 * GLINT_BAND
        progress = (frame - GLINT_START) / (GLINT_END - 1 - GLINT_START)
        return -GLINT_BAND + span * progress

    frames: list[Frame] = []
    previous: dict[tuple[int, int], bytes] | None = None
    for t in range(LOGO_TOTAL_FRAMES):
        frame = Frame()
        current = cell_bytes(render_logo_frame(rows, x0, y0, glint_pos(t)))
        if previous is None:
            # Initial draw happens behind a lights-out palette (BGP=0 until the
            # fade begins), so it can safely span the frame budget.
            frame.copy(VRAM_TILES + 16, b"".join(current[cell] for cell in active))
            for cy in range(cy0, cy1 + 1):
                row_indexes = bytes(index[(cx, cy)] for cx in range(cx0, cx1 + 1))
                frame.copy(VRAM_MAP + cy * 32 + cx0, row_indexes)
            frame.io(((0xFF40, 0x91),))  # LCD + background on
        else:
            changed = [
                (VRAM_TILES + index[cell] * 16, data)
                for cell, data in current.items()
                if previous[cell] != data
            ]
            for dst, data in merge_runs(changed):
                frame.copy(dst, data)
        previous = current

        for fade_frame, bgp in FADE_STEPS:
            if t == fade_frame:
                frame.io(((0xFF47, bgp),))
        if t == CHIME1_FRAME:
            frame.io(CHIME1_WRITES)
        if t == CHIME2_FRAME:
            frame.io(CHIME2_WRITES)
        frames.append(frame)
    return frames


# ---------------------------------------------------------------------------
# Legacy video mode

def extract_frames(ffmpeg: Path, video: Path) -> list[bytes]:
    command = [
        str(ffmpeg), "-v", "error", "-ss", "0", "-t", "5", "-i", str(video),
        "-vf",
        "fps=6,scale=160:144:force_original_aspect_ratio=increase,"
        "crop=160:144,format=gray",
        "-frames:v", str(VIDEO_FRAME_COUNT), "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.check_output(command)
    stride = WIDTH * HEIGHT
    if len(raw) != VIDEO_FRAME_COUNT * stride:
        raise RuntimeError(f"expected {VIDEO_FRAME_COUNT} frames, got {len(raw) / stride:.2f}")
    return [raw[i * stride : (i + 1) * stride] for i in range(VIDEO_FRAME_COUNT)]


def load_gray_frames(path: Path) -> list[bytes]:
    raw = path.read_bytes()
    stride = WIDTH * HEIGHT
    if len(raw) != VIDEO_FRAME_COUNT * stride:
        raise RuntimeError(f"expected {VIDEO_FRAME_COUNT} Gray8 frames, got {len(raw) / stride:.2f}")
    return [raw[i * stride : (i + 1) * stride] for i in range(VIDEO_FRAME_COUNT)]


def quantize(gray: bytes) -> list[int]:
    # Direct four-level quantization avoids exceeding the DMG's 256-tile limit.
    return [3 - min(3, (value + 42) // 85) for value in gray]


def encode_video_frame(pixels: list[int]) -> tuple[bytes, bytes]:
    tile_data = bytearray()
    tile_map = bytearray(TILE_MAP_BYTES)
    known: dict[bytes, int] = {}
    for cy in range(CELLS_Y):
        for cx in range(CELLS_X):
            tile = encode_cell(pixels, cx, cy)
            if tile not in known:
                if len(known) == 256:
                    raise RuntimeError("frame needs more than 256 unique Game Boy tiles")
                known[tile] = len(known)
                tile_data.extend(tile)
            tile_map[cy * 32 + cx] = known[tile]
    tile_data.extend(bytes(TILE_DATA_BYTES - len(tile_data)))
    return bytes(tile_data), bytes(tile_map)


def video_frames(gray_frames: list[bytes]) -> list[Frame]:
    frames: list[Frame] = []
    for i, gray in enumerate(gray_frames):
        tile_data, tile_map = encode_video_frame(quantize(gray))
        frame = Frame(step=10)  # 6 fps on a 59.7 Hz machine
        frame.copy(VRAM_TILES, tile_data)
        frame.copy(VRAM_MAP, tile_map)
        if i == 0:
            frame.io(((0xFF47, 0xE4), (0xFF40, 0x91)))
        frames.append(frame)
    return frames


# ---------------------------------------------------------------------------
# Playback engine
#
# Descriptor stream, one record per frame:
#   [n_copies][delayL][delayH] then n x [bank][dstL][dstH][srcL][srcH][lenL][lenH]
# Copy sources live in the banked window at $4000 (bank via $FF51); the copy
# destination is any bus address, which is what lets palette/APU/LCDC writes
# ride the same path as tile data.

# T-cycle costs of the emitted program, used to solve each frame's delay so a
# record occupies exactly `step` display frames (70224 T-cycles each).
CYC_FRAME_BASE = 60 + 24 + 32 + 40 - 4        # header + copy check + delay load + loop counter
CYC_NO_COPIES = 4                             # JR Z taken instead of fall-through
CYC_PER_COPY = 204                            # entry reads + push/pop + count bookkeeping
CYC_PER_BYTE = 52                             # LD A,(DE); INC DE; LD (HL+),A; DEC BC; A=B|C; JR
CYC_LAST_COPY = -4                            # final JR NZ,copy_entry not taken
CYC_PER_DELAY = 28                            # DEC BC; LD A,B; OR C; JR NZ


def make_program(frame_count: int) -> bytes:
    asm = Assembler(MAIN)
    asm.emit(0xF3, 0x31, 0xFE, 0xFF)               # DI; LD SP,$FFFE
    asm.emit(0xAF, 0xE0, 0x40)                     # LCD off
    asm.emit(0xE0, 0x42, 0xE0, 0x43)               # SCY = SCX = 0
    asm.emit(0xE0, 0x47)                           # BGP all-lightest (fade-in start)
    asm.emit(0x3E, frame_count, 0xE0, 0x80)        # frame counter in HRAM
    asm.emit(0x21, DESCRIPTORS & 0xFF, DESCRIPTORS >> 8)

    asm.label("frame")
    asm.emit(0x2A, 0xE0, 0x81)                     # copy count
    asm.emit(0x2A, 0xE0, 0x83, 0x2A, 0xE0, 0x84)   # delay lo/hi
    asm.emit(0xF0, 0x81, 0xB7)                     # any copies?
    asm.jr(0x28, "after_copies")

    asm.label("copy_entry")
    asm.emit(0x2A, 0xE0, 0x51)                     # select boot-data bank
    asm.emit(0x2A, 0xE0, 0x86, 0x2A, 0xE0, 0x87)   # stash destination
    asm.emit(0x2A, 0x5F, 0x2A, 0x57)               # source -> DE
    asm.emit(0x2A, 0x4F, 0x2A, 0x47)               # length -> BC
    asm.emit(0xE5)                                 # save descriptor cursor
    asm.emit(0xF0, 0x86, 0x6F, 0xF0, 0x87, 0x67)   # destination -> HL
    asm.label("copy_loop")
    asm.emit(0x1A, 0x13, 0x22, 0x0B, 0x78, 0xB1)   # LD A,(DE); INC DE; LD (HL+),A; DEC BC; B|C
    asm.jr(0x20, "copy_loop")
    asm.emit(0xE1)                                 # restore descriptor cursor
    asm.emit(0xF0, 0x81, 0x3D, 0xE0, 0x81)         # next copy
    asm.jr(0x20, "copy_entry")

    asm.label("after_copies")
    asm.emit(0xF0, 0x83, 0x4F, 0xF0, 0x84, 0x47)   # delay -> BC
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


def build_rom(frames: list[Frame]) -> bytes:
    if len(frames) > 255:
        raise RuntimeError("more than 255 animation frames")

    banks: list[bytearray] = [bytearray()]

    def store(data: bytes) -> tuple[int, int]:
        if len(data) > BANK_SIZE:
            raise RuntimeError("copy source larger than a bank")
        if len(banks[-1]) + len(data) > BANK_SIZE:
            banks.append(bytearray())
        bank, address = len(banks) - 1, 0x4000 + len(banks[-1])
        banks[-1].extend(data)
        return bank, address

    descriptors = bytearray()
    for frame in frames:
        work = CYC_FRAME_BASE
        if frame.copies:
            work += sum(CYC_PER_COPY + CYC_PER_BYTE * len(d) for _, d in frame.copies)
            work += CYC_LAST_COPY
        else:
            work += CYC_NO_COPIES
        delay = max(1, (frame.step * DMG_FRAME_CYCLES - work) // CYC_PER_DELAY)
        if delay > 0xFFFF:
            raise RuntimeError("frame delay exceeds 16 bits")

        descriptors.append(len(frame.copies))
        descriptors.extend((delay & 0xFF, delay >> 8))
        for dst, data in frame.copies:
            bank, src = store(data)
            descriptors.append(bank)
            descriptors.extend((dst & 0xFF, dst >> 8))
            descriptors.extend((src & 0xFF, src >> 8))
            descriptors.extend((len(data) & 0xFF, len(data) >> 8))

    program = make_program(len(frames))
    if MAIN + len(program) > DESCRIPTORS:
        raise RuntimeError("boot program overlaps the descriptor area")
    if DESCRIPTORS + len(descriptors) > FIXED_SIZE:
        raise RuntimeError("descriptors exceed the fixed bank")

    fixed = bytearray(FIXED_SIZE)
    fixed[0:3] = bytes((0xC3, MAIN & 0xFF, MAIN >> 8))
    fixed[0xFE:0x100] = bytes((0xE0, 0x50))         # unmap; next fetch is cart $0100
    fixed[MAIN : MAIN + len(program)] = program
    fixed[DESCRIPTORS : DESCRIPTORS + len(descriptors)] = descriptors
    return bytes(fixed + b"".join(bank.ljust(BANK_SIZE, b"\x00") for bank in banks))


def write_header(rom: bytes, output: Path, source: str) -> None:
    lines = [
        "#pragma once", "#include <cstddef>", "#include <cstdint>", "",
        f"// Generated by tools/make_boot_rom.py - {source}.",
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
    parser.add_argument("--logo", type=Path, help="ASCII logo bitmap ('.'/'#') for the glint boot")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.logo:
        frames = logo_frames(load_logo(args.logo))
        source = "POKEBOY logo, fade + chime + glint at 60 fps"
    elif args.raw_gray:
        frames = video_frames(load_gray_frames(args.raw_gray))
        source = "video slideshow, 0:00-0:05 at 6 fps"
    elif args.video and args.ffmpeg:
        frames = video_frames(extract_frames(args.ffmpeg, args.video))
        source = "video slideshow, 0:00-0:05 at 6 fps"
    else:
        parser.error("provide --logo, --raw-gray, or video and --ffmpeg")
    write_header(build_rom(frames), args.output, source)


if __name__ == "__main__":
    main()
