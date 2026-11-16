"""App icons must keep the source's transparent background, and every named icon must ship."""

import re
import struct
import unittest
import zlib

import _bootstrap

ROOT = _bootstrap.ROOT
UI_ICONS = _bootstrap.SRC / "productime" / "ui" / "icons"
ACTIONS = UI_ICONS / "hicolor" / "scalable" / "actions"
APP_ICONS = ROOT / "packaging" / "icons" / "hicolor"
DERIVED = {
    **{APP_ICONS / f"{s}x{s}" / "apps" / "org.productime.Productime.png": s for s in (64, 128, 256, 512)},
    UI_ICONS / "productime-brand-64.png": 64,
    UI_ICONS / "productime-brand-128.png": 128,
}
RGBA = 6


def png_header_and_first_pixel(path):
    """Width, height, colour type, and the top-left RGBA pixel, decoded with the stdlib only."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG")
    offset, idat, header = 8, b"", None
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height, depth, colour, _compression, _filter, interlace = header
    pixel = None
    if colour == RGBA and depth == 8 and interlace == 0:
        # The first scanline's filter only looks left, so its first pixel is the raw bytes.
        raw = zlib.decompressobj().decompress(idat, 1 + width * 4)
        pixel = tuple(raw[1:5])
    return width, height, colour, pixel


class AppIconTests(unittest.TestCase):
    def test_every_derived_icon_is_transparent_rgba_at_its_size(self):
        for path, size in DERIVED.items():
            with self.subTest(icon=str(path.relative_to(ROOT))):
                width, height, colour, pixel = png_header_and_first_pixel(path)
                self.assertEqual((width, height), (size, size))
                self.assertEqual(colour, RGBA, "icon has no alpha channel")
                self.assertEqual(pixel[3], 0, "the corner should be fully transparent")

    def test_every_productime_symbolic_icon_named_in_the_source_ships(self):
        names = set()
        for path in (_bootstrap.SRC / "productime").rglob("*.py"):
            names.update(re.findall(r"productime-[a-z-]+-symbolic", path.read_text(encoding="utf-8")))
        self.assertTrue(names)
        missing = sorted(name for name in names if not (ACTIONS / f"{name}.svg").is_file())
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
