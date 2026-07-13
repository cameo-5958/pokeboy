#!/usr/bin/env python3
"""Assemble and package Breadwinner: Battle Link for the guarded Red ROM."""

import json
import subprocess
import tempfile
from pathlib import Path

from compile_mod import compile_manifest


ROOT = Path(__file__).resolve().parents[2]
MODS = ROOT / "gameboy" / "mods"
ROM = ROOT / "backend" / "roms" / "pokemon-red.gb"
OUTPUT = ROOT / "backend" / "mods" / "battle-link.gbmod"
BINARY = MODS / "battle-link.bin"
MEDICINE_BINARY = MODS / "battle-link-medicine.bin"
BANK = 0x0F
ADDRESS = 0x7C00
MEDICINE_BANK = 0x03
MEDICINE_ADDRESS = 0x7D00


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="battle-link-") as directory:
        temp = Path(directory)
        obj = temp / "battle-link.o"
        image = temp / "battle-link.gb"
        symbols = temp / "battle-link.sym"
        subprocess.run(["rgbasm", "-o", obj, MODS / "battle-link.asm"], check=True)
        subprocess.run(
            ["rgblink", "-O", ROM, "-o", image, "-n", symbols, obj],
            check=True,
        )
        rom = image.read_bytes()
        start = BANK * 0x4000 + (ADDRESS - 0x4000)
        exported = {}
        for line in symbols.read_text(encoding="utf-8").splitlines():
            location, name = line.split(maxsplit=1)
            if ":" not in location:
                continue
            _, raw_address = location.split(":", 1)
            if name in {"BattleLinkBattleStart", "BattleLinkSelect",
                        "BattleLinkDispatch", "BattleLinkChooseSwitch",
                        "BattleLinkAwaitSwitch", "BattleLinkAwaitItem",
                        "BattleLinkEnd", "BattleLinkHostSlot",
                        "BattleLinkStartHostSlot", "BattleLinkAwaitHostSlot",
                        "BattleLinkMedicineStatusGate",
                        "BattleLinkMedicineReviveGate",
                        "BattleLinkMedicineHpGate", "BattleLinkPPRestoreGate",
                        "BattleLinkMedicineEnd"}:
                exported[name] = int(raw_address, 16)
        if len(exported) != 15:
            raise RuntimeError("assembler did not export required Battle Link symbols")
        code = rom[start:start + exported["BattleLinkEnd"] - ADDRESS]
        medicine_start = MEDICINE_BANK * 0x4000 + (MEDICINE_ADDRESS - 0x4000)
        medicine = rom[medicine_start:medicine_start
                       + exported["BattleLinkMedicineEnd"] - MEDICINE_ADDRESS]
        if not code or not medicine:
            raise RuntimeError("an assembled Battle Link section is empty")

    manifest = json.loads((MODS / "battle-link.mod.json").read_text(encoding="utf-8"))
    BINARY.write_bytes(code)
    MEDICINE_BINARY.write_bytes(medicine)
    manifest["targetRom"] = str(ROM)
    for symbol in manifest["symbols"]:
        base_address = (MEDICINE_ADDRESS if symbol["section"] == "battle-link.medicine"
                        else ADDRESS)
        actual = exported[symbol["name"]] - base_address
        if symbol["offset"] != actual:
            raise RuntimeError(f"manifest offset for {symbol['name']} is {symbol['offset']}, expected {actual}")
    for relocation, label in zip(
        manifest["relocations"][:3],
        ("BattleLinkStartHostSlot", "BattleLinkHostSlot",
         "BattleLinkAwaitHostSlot"),
    ):
        actual = exported[label] - ADDRESS
        if relocation["offset"] != actual:
            raise RuntimeError(f"manifest offset for {label} is {relocation['offset']}, expected {actual}")
    package = compile_manifest(manifest, MODS)
    OUTPUT.write_bytes(package)
    print(f"wrote {OUTPUT} ({len(package)} bytes, {len(code)} bytes of ROM code)")


if __name__ == "__main__":
    main()
