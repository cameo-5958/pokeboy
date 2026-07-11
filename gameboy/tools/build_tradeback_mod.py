#!/usr/bin/env python3
"""Build the bundled Pokemon Red Pokemon Center tradeback mod."""

import sys
from pathlib import Path

from compile_mod import compile_manifest


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "backend/mods/tradeback-npc.gbmod"

def rom_reloc(section, offset, kind, name):
    return {"target": "section", "in": section, "offset": offset,
            "type": kind, "reference": "rom", "name": name}


def build_document():
    entry = "fa 63 d1 a7 c8 cd 00 00 af ea 3d cd fa 64 d1 ea 0f cd ea 34 cd 11 13 cd 06 00 21 00 00 cd 00 00 fa 64 d1 11 1e cd 06 00 21 00 00 cd 00 00 06 00 21 00 00 cd 00 00 c9"
    helpers = "af ea 92 cf fa 64 d1 ea 91 cf a7 c9 af ea 92 cf 3e 01 ea d4 cc 3e 32 ea 2b d1 06 00 21 00 00 cd 00 00 af ea 2b d1 cd 00 00 c9"
    patches = [
        # Every Pokemon Center's f6 text command reaches this shared handler.
        # Bank-switch to the mod entry rather than patching each text script;
        # those scripts live in several different switchable ROM banks.
        {"symbol": "CableClubNPC",
         "expected": "21 b8 72 cd 49 3c fa 4b d7",
         "data": "3e 12 21 00 00 cd 00 00 c9"},
        {"symbol": "InGameTrade_DoTrade", "addend": 8,
         "expected": "cd fc 13", "data": "cd 00 00"},
        {"symbol": "InGameTrade_DoTrade", "addend": 96,
         "expected": "fa 34 cd ea 91 cf af ea 49 cc ea 95 cf cd 1f 39 3e 80 ea 49 cc cd 27 39 cd 19 5d 21 7d 7d 06 05 cd d6 35",
         "data": "cd 00 00" + " 00" * 32},
    ]
    relocations = [
        rom_reloc("tradeback.entry", 6, "call16", "SaveScreenTilesToBuffer2"),
        rom_reloc("tradeback.entry", 25, "bank8", "InGameTrade_GetMonName"),
        rom_reloc("tradeback.entry", 27, "abs16", "InGameTrade_GetMonName"),
        rom_reloc("tradeback.entry", 30, "call16", "Bankswitch"),
        rom_reloc("tradeback.entry", 39, "bank8", "InGameTrade_GetMonName"),
        rom_reloc("tradeback.entry", 41, "abs16", "InGameTrade_GetMonName"),
        rom_reloc("tradeback.entry", 44, "call16", "Bankswitch"),
        rom_reloc("tradeback.entry", 47, "bank8", "InGameTrade_DoTrade"),
        rom_reloc("tradeback.entry", 49, "abs16", "InGameTrade_DoTrade"),
        rom_reloc("tradeback.entry", 52, "call16", "Bankswitch"),
        rom_reloc("trade.helpers", 27, "bank8", "TryEvolvingMon"),
        rom_reloc("trade.helpers", 29, "abs16", "TryEvolvingMon"),
        rom_reloc("trade.helpers", 32, "call16", "Bankswitch"),
        rom_reloc("trade.helpers", 39, "call16", "PlayDefaultMusic"),
    ]
    relocations.extend([
        {"target": "patch", "in": "0", "offset": 3, "type": "abs16",
         "reference": "module", "name": "TradebackEntry"},
        {"target": "patch", "in": "0", "offset": 6, "type": "call16",
         "reference": "rom", "name": "Bankswitch"},
        {"target": "patch", "in": "1", "offset": 1,
         "type": "call16", "reference": "module", "name": "SelectFirstPartyMon"},
        {"target": "patch", "in": "2", "offset": 1,
         "type": "call16", "reference": "module", "name": "FinishTradeback"},
    ])
    return {
        "id": "tradeback-npc", "name": "TRADEBACK NPC",
        "targetCrc32": "0x9f7fdd53", "targetSize": 1048576,
        "metadata": {
            "version": "1.2.0",
            "description": "Replaces every Pokemon Center link receptionist with a native tradeback NPC for party slot one.",
            "config": [{"key": "enabled", "label": "TRADEBACK NPC", "type": "toggle",
                        "default": True, "description": "Let every Pokemon Center link receptionist trade back the first Pokemon in your party."}],
        },
        "sections": [
            {"name": "tradeback.entry", "placement": "fixed", "bank": "0x12",
             "address": "0x638f", "data": entry},
            {"name": "trade.helpers", "placement": "fixed", "bank": "0x1c",
             "address": "0x7b9d", "data": helpers},
        ],
        "patches": patches,
        "symbols": [
            {"name": "TradebackEntry", "section": "tradeback.entry"},
            {"name": "SelectFirstPartyMon", "section": "trade.helpers"},
            {"name": "FinishTradeback", "section": "trade.helpers", "offset": 12},
        ],
        "relocations": relocations,
    }


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT
    package = compile_manifest(build_document(), Path.cwd())
    output.write_bytes(package)
    print(f"wrote {output} ({len(package)} bytes)")
