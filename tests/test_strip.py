"""Tests for the request strip codec, the TGA reader and the Lua encoder."""
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from mforge import stripcodec as sc
from mforge.errors import ValidationError


def make_tga(cells, cell_px=8, per_row=100, width=900, height=120, rle=False, top_left=True):
    """Draw cells into an uncompressed (or RLE) 24-bit TGA the way the game would."""
    pixels = [[(0, 0, 0)] * width for _ in range(height)]
    for i, cell in enumerate(cells):
        x0, y0 = (i % per_row) * cell_px, (i // per_row) * cell_px
        color = (255 * (cell >> 2 & 1), 255 * (cell >> 1 & 1), 255 * (cell & 1))
        for y in range(y0, y0 + cell_px):
            for x in range(x0, x0 + cell_px):
                pixels[y][x] = color
    order = pixels if top_left else pixels[::-1]
    raw = b"".join(bytes((b, g, r)) for row in order for (r, g, b) in row)
    if rle:
        out = bytearray()
        for i in range(0, len(raw), 3):
            out += bytes([0x80]) + raw[i:i + 3]
        raw = bytes(out)
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10 if rle else 2, 0, 0, 0, 0, 0,
                         width, height, 24, 0x20 if top_left else 0)
    return header + raw


class StripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_roundtrip_cells(self):
        for payload in (b"", b"a", b"MFSHOT 1700000000", bytes(range(256)) * 3):
            cells = sc.encode_cells(4242, payload)
            self.assertTrue(all(0 <= c < 8 for c in cells))
            self.assertEqual(sc.decode_cells(cells), (4242, payload))
            self.assertEqual(len(cells), sc.cells_needed(len(payload)))

    def test_corruption_is_detected(self):
        cells = sc.encode_cells(1, b"hello world")
        cells[20] ^= 1
        with self.assertRaises(ValidationError):
            sc.decode_cells(cells)
        with self.assertRaises(ValidationError):
            sc.decode_cells(cells[:10])

    def test_tga_variants(self):
        cells = sc.encode_cells(7, b"pixels")
        for rle in (False, True):
            for top_left in (True, False):
                path = self.tmp / f"s{rle}{top_left}.tga"
                path.write_bytes(make_tga(cells, rle=rle, top_left=top_left))
                message_id, payload, details = sc.find_strip(sc.read_tga(path))
                self.assertEqual((message_id, payload), (7, b"pixels"))
                self.assertEqual(details["cell_px"], 8)

    def test_strip_found_at_offset(self):
        cells = sc.encode_cells(9, b"shifted")
        path = self.tmp / "o.tga"
        data = make_tga(cells, width=920, height=140)
        path.write_bytes(data)
        image = sc.read_tga(path)
        self.assertEqual(sc.find_strip(image)[1], b"shifted")

    def test_bad_files(self):
        for name, data in (("short", b"x"), ("junk", b"\0" * 40),
                           ("trunc", make_tga(sc.encode_cells(1, b"a"))[:100])):
            path = self.tmp / name
            path.write_bytes(data)
            with self.assertRaises(ValidationError):
                sc.find_strip(sc.read_tga(path))

    def test_blank_picture_has_no_strip(self):
        path = self.tmp / "blank.tga"
        path.write_bytes(make_tga([]))
        with self.assertRaises(ValidationError):
            sc.find_strip(sc.read_tga(path))


if __name__ == "__main__":
    unittest.main()


class LuaCodecTests(unittest.TestCase):
    """The Lua encoder must produce exactly the cells the Python decoder expects."""

    def test_lua_matches_python(self):
        import subprocess
        from tests.test_lua_addon import find_luajit
        from tests.harness import REPO
        luajit = find_luajit()
        if not luajit:
            self.skipTest("LuaJIT not installed")
        cases = [(4242, "MFSHOT 1700000000"), (0, ""), (65535, "x" * 300), (7, "tab\tq\"\\")]
        script = (
            'dofile(arg[1] .. "/addon/MacroForge/Codec.lua")\n'
            'local cases = { %s }\n'
            'for _, c in ipairs(cases) do\n'
            '  print(table.concat(MacroForgeCodec.Encode(c[1], c[2]), ""))\nend\n'
        ) % ", ".join('{%d, "%s"}' % (i, p.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\t"))
                      for i, p in cases)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.lua"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run([luajit, str(path), str(REPO)], capture_output=True, text=True,
                                 timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.split()
        for line, (ident, payload) in zip(lines, cases):
            expected = "".join(str(c) for c in sc.encode_cells(ident, payload.encode()))
            self.assertEqual(line, expected)
        self.assertEqual(len(lines), len(cases))
