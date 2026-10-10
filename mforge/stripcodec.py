"""Data strips: bytes packed into colored squares that the addon draws and a screenshot preserves.

The addon (``addon/MacroForge/Codec.lua``) turns a message into 3-bit cells, each drawn as one
square whose red, green and blue channels are fully on or off (eight pure colors, which survive any
gamma or contrast setting). This module is the other half: it builds the same cells (for tests) and
reads them back out of a screenshot saved by the game, using only the standard library (TGA files).

Message layout: ``C7 1A``, 2-byte id, 2-byte payload length, payload, then a Fletcher-16 checksum
(two bytes) of everything after the magic. Bytes are packed MSB first into 3-bit cells.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError

MAGIC = (0xC7, 0x1A)
BITS_PER_CELL = 3
CELLS_PER_ROW = 100
MAX_PAYLOAD = 3200
MAX_IMAGE_PIXELS = 40_000_000


# -- cells <-> bytes -------------------------------------------------------------------------

def fletcher16(data: bytes) -> tuple[int, int]:
    """The two checksum bytes the addon computes (sums modulo 255)."""
    s1 = s2 = 0
    for byte in data:
        s1 = (s1 + byte) % 255
        s2 = (s2 + s1) % 255
    return s1, s2


def encode_cells(message_id: int, payload: bytes) -> list[int]:
    """Cells (0..7) for a message, exactly as the Lua encoder produces them."""
    if len(payload) > MAX_PAYLOAD:
        raise ValidationError("payload is too long for one strip")
    body = bytes([(message_id >> 8) & 255, message_id & 255,
                  (len(payload) >> 8) & 255, len(payload) & 255]) + payload
    data = bytes(MAGIC) + body + bytes(fletcher16(body))
    cells: list[int] = []
    accumulator = bits = 0
    for byte in data:
        accumulator = (accumulator << 8) | byte
        bits += 8
        while bits >= BITS_PER_CELL:
            bits -= BITS_PER_CELL
            cells.append((accumulator >> bits) & 7)
            accumulator &= (1 << bits) - 1
    if bits:
        cells.append((accumulator << (BITS_PER_CELL - bits)) & 7)
    return cells


def cells_to_bytes(cells: list[int]) -> bytes:
    """Unpack cells into bytes (a trailing partial byte is dropped)."""
    out = bytearray()
    accumulator = bits = 0
    for cell in cells:
        accumulator = (accumulator << BITS_PER_CELL) | (cell & 7)
        bits += BITS_PER_CELL
        while bits >= 8:
            bits -= 8
            out.append((accumulator >> bits) & 255)
            accumulator &= (1 << bits) - 1
    return bytes(out)


def decode_cells(cells: list[int]) -> tuple[int, bytes]:
    """Return ``(message id, payload)`` from cells; raise if magic, length or checksum are wrong."""
    data = cells_to_bytes(cells)
    if len(data) < 8 or (data[0], data[1]) != MAGIC:
        raise ValidationError("no strip header found")
    length = (data[4] << 8) | data[5]
    if length > MAX_PAYLOAD or len(data) < 6 + length + 2:
        raise ValidationError("strip is truncated or has a bad length")
    body = data[2:6 + length]
    if tuple(data[6 + length:8 + length]) != fletcher16(body):
        raise ValidationError("strip checksum does not match")
    return (data[2] << 8) | data[3], bytes(data[6:6 + length])


def cells_needed(payload_length: int) -> int:
    """How many cells a payload of this size takes."""
    return -(-((payload_length + 8) * 8) // BITS_PER_CELL)


# -- TGA images ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Image:
    """A decoded picture: ``rows[y]`` holds ``width * channels`` bytes (RGB[A]), top row first."""

    width: int
    height: int
    channels: int
    rows: tuple[bytes, ...]

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """RGB of one pixel (raises IndexError outside the picture)."""
        base = x * self.channels
        row = self.rows[y]
        return row[base], row[base + 1], row[base + 2]


def read_tga(path: Path) -> Image:
    """Read an uncompressed or run-length-encoded 24/32-bit TGA file."""
    data = Path(path).read_bytes()
    if len(data) < 18:
        raise ValidationError("not a TGA file (too short)")
    id_length, color_map_type, image_type = data[0], data[1], data[2]
    color_map_length = int.from_bytes(data[5:7], "little")
    color_map_bits = data[7]
    width = int.from_bytes(data[12:14], "little")
    height = int.from_bytes(data[14:16], "little")
    bits, descriptor = data[16], data[17]
    if image_type not in (2, 10) or bits not in (24, 32):
        raise ValidationError(f"unsupported TGA (type {image_type}, {bits} bits)")
    if not 0 < width * height <= MAX_IMAGE_PIXELS:
        raise ValidationError("TGA has an unreasonable size")
    channels = bits // 8
    palette = color_map_length * ((color_map_bits + 7) // 8) if color_map_type else 0
    offset = 18 + id_length + palette
    total = width * height * channels
    if image_type == 2:
        raw = data[offset:offset + total]
    else:
        raw = _unpack_rle(data, offset, width * height, channels)
    if len(raw) < total:
        raise ValidationError("TGA pixel data is truncated")
    rows = []
    for y in range(height):
        row = bytearray(raw[y * width * channels:(y + 1) * width * channels])
        for i in range(0, len(row), channels):  # stored as B, G, R(, A): swap to R, G, B
            row[i], row[i + 2] = row[i + 2], row[i]
        rows.append(bytes(row))
    if not descriptor & 0x20:  # bottom-left origin: the first stored row is the bottom one
        rows.reverse()
    return Image(width, height, channels, tuple(rows))


def _unpack_rle(data: bytes, offset: int, pixels: int, channels: int) -> bytes:
    out = bytearray()
    position = offset
    produced = 0
    while produced < pixels and position < len(data):
        header = data[position]
        position += 1
        count = (header & 0x7F) + 1
        if header & 0x80:
            out += data[position:position + channels] * count
            position += channels
        else:
            out += data[position:position + count * channels]
            position += count * channels
        produced += count
    return bytes(out)


# -- finding the strip in a picture -----------------------------------------------------------

def _cell_value(image: Image, x: int, y: int) -> int:
    red, green, blue = image.pixel(x, y)
    return (4 if red >= 128 else 0) | (2 if green >= 128 else 0) | (1 if blue >= 128 else 0)


def read_strip(image: Image, cell_px: float, origin_x: float = 0, origin_y: float = 0,
               per_row: int = CELLS_PER_ROW) -> list[int]:
    """Sample the cell centers of a strip whose top-left corner is at ``(origin_x, origin_y)``."""
    cells: list[int] = []
    row = 0
    while True:
        y = int(origin_y + (row + 0.5) * cell_px)
        if y >= image.height:
            break
        for col in range(per_row):
            x = int(origin_x + (col + 0.5) * cell_px)
            if x >= image.width:
                return cells
            cells.append(_cell_value(image, x, y))
        row += 1
        if row * per_row > MAX_PAYLOAD * 3:
            break
    return cells


CELL_SIZES = (8, 6, 4, 10, 12, 16, 5, 7)


def find_strip(image: Image, cell_sizes: tuple[float, ...] = CELL_SIZES) -> tuple[int, bytes, dict]:
    """Search small offsets and common cell sizes for a valid strip.

    Returns ``(message id, payload, details)`` where details says which geometry matched.
    """
    for cell in cell_sizes:
        for origin_y in range(0, 12):
            for origin_x in range(0, 12):
                head = read_strip(image, cell, origin_x, origin_y)[:8]
                if cells_to_bytes(head)[:2] != bytes(MAGIC):
                    continue
                try:
                    message_id, payload = decode_cells(read_strip(image, cell, origin_x, origin_y))
                except ValidationError:
                    continue
                return message_id, payload, {"cell_px": cell, "origin": (origin_x, origin_y)}
    raise ValidationError("no valid data strip found in the picture")
