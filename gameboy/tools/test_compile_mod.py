import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compile_mod import CompileError, compile_manifest


class CompileModTests(unittest.TestCase):
    def test_compiles_every_table_and_blob(self):
        manifest = {
            "id": "tiny-test",
            "name": "Tiny Test",
            "targetCrc32": "0x12345678",
            "targetSize": 32768,
            "metadata": {"version": "1.0.0"},
            "imports": ["test.tick"],
            "sections": [{"name": "code", "placement": "append",
                          "alignment": 16, "data": "d3 00 00 c9"}],
            "patches": [{"symbol": "Hook", "expected": "00 00 00",
                         "data": "cd 00 00"}],
            "symbols": [{"name": "Entry", "section": "code", "offset": 0}],
            "relocations": [
                {"target": "section", "in": "code", "offset": 1,
                 "type": "host16", "reference": "host", "name": "test.tick"},
                {"target": "patch", "in": "0", "offset": 1,
                 "type": "call16", "reference": "module", "name": "Entry"},
            ],
        }
        package = compile_manifest(manifest, Path("."))
        self.assertEqual(package[:8], b"GBMOD1\r\n")
        self.assertEqual(struct.unpack_from("<HH", package, 8), (1, 1))
        self.assertEqual(struct.unpack_from("<I", package, 16)[0], len(package))
        self.assertEqual(struct.unpack_from("<II", package, 24), (0x12345678, 32768))
        self.assertEqual(struct.unpack_from("<II", package, 56), (96, 1))
        self.assertEqual(struct.unpack_from("<II", package, 64), (104, 1))
        self.assertIn(b'{"version":"1.0.0"}', package)
        self.assertTrue(package.endswith(bytes.fromhex("d30000c9cd0000000000")))

    def test_reads_relative_files_and_computes_rom_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "code.bin").write_bytes(b"\xc9")
            (base / "game.gb").write_bytes(b"\0" * 32)
            package = compile_manifest({
                "id": "file-test", "targetRom": "game.gb",
                "sections": [{"name": "code", "data": {"file": "code.bin"}}],
            }, base)
            self.assertEqual(struct.unpack_from("<I", package, 28)[0], 32)
            self.assertEqual(package[-1], 0xc9)

    def test_rejects_invalid_host_relocation(self):
        with self.assertRaisesRegex(CompileError, "host16 requires"):
            compile_manifest({
                "id": "bad", "sections": [{"name": "code", "data": "0000"}],
                "relocations": [{"target": "section", "in": "code", "offset": 0,
                                  "type": "host16", "reference": "rom", "name": "X"}],
            }, Path("."))

    def test_rejects_relocation_not_attached_to_opcode(self):
        with self.assertRaisesRegex(CompileError, "call16 must immediately follow"):
            compile_manifest({
                "id": "bad-call",
                "sections": [{"name": "code", "data": "00 00 00"}],
                "symbols": [{"name": "Entry", "section": "code"}],
                "relocations": [{"target": "section", "in": "code", "offset": 1,
                                  "type": "call16", "reference": "module",
                                  "name": "Entry"}],
            }, Path("."))


if __name__ == "__main__":
    unittest.main()
