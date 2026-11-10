"""Image conversion for keys and the touch strip."""
from __future__ import annotations

import io
from typing import Tuple, Union

from PIL import Image

ImageLike = Union[Image.Image, str, bytes]


def load(image: ImageLike) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    return Image.open(image)


def prepare(image: ImageLike, size: Tuple[int, int]) -> Image.Image:
    """RGB image of exactly `size`; transparency is flattened onto black."""
    im = load(image)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    if im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return im


def to_jpeg(image: ImageLike, size: Tuple[int, int], quality: int = 92) -> bytes:
    """Baseline JPEG, 4:4:4 (the firmware decoder does not read progressive files)."""
    buf = io.BytesIO()
    prepare(image, size).save(buf, "JPEG", quality=quality, subsampling=0, progressive=False, optimize=False)
    return buf.getvalue()


def to_rgb565(image: ImageLike, size: Tuple[int, int]) -> bytes:
    raw = prepare(image, size).tobytes()
    out = bytearray(len(raw) // 3 * 2)
    for i in range(len(raw) // 3):
        r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[2 * i] = v & 0xFF
        out[2 * i + 1] = v >> 8
    return bytes(out)


def rgb565(color: Tuple[int, int, int]) -> int:
    r, g, b = color
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
