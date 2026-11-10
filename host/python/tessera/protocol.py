"""Tessera Deck Protocol (TDP) v1: constants, framing and payload codecs.

The normative description is docs/protocol.md.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

PROTO_MAJOR = 1

# USB identity. Placeholder IDs for development - see docs/protocol.md, "USB identity".
USB_VID = 0x1209
USB_PID = 0x0001
USB_PRODUCT = "Tessera Deck"

SOF = b"TD"
HEADER_LEN = 8
CRC_LEN = 4

CMD_GET_INFO = 0x01
CMD_PING = 0x02
CMD_GET_STATE = 0x03
CMD_GET_CONFIG = 0x04
CMD_SET_CONFIG = 0x05
CMD_SET_BRIGHTNESS = 0x10
CMD_SET_POWER = 0x11
CMD_SET_KEY_IMAGE = 0x12
CMD_SET_STRIP_IMAGE = 0x13
CMD_FILL = 0x14
CMD_RESET_DISPLAY = 0x15
CMD_SAVE_SPLASH = 0x16
CMD_CLEAR_SPLASH = 0x17
CMD_REBOOT = 0x20

RSP_ACK = 0x80
RSP_INFO = 0x81
RSP_STATE = 0x82
RSP_CONFIG = 0x83

EVT_KEY = 0xA0
EVT_ENC_ROTATE = 0xA1
EVT_ENC_BUTTON = 0xA2
EVT_TOUCH = 0xA3
EVT_POWER = 0xA4

FMT_RGB565 = 0
FMT_JPEG = 1

FILL_KEY = 0
FILL_STRIP = 1
FILL_ALL_KEYS = 2
FILL_ALL = 3

TOUCH_TAP = 0
TOUCH_LONG = 1
TOUCH_DRAG = 2
TOUCH_KINDS = {TOUCH_TAP: "tap", TOUCH_LONG: "long", TOUCH_DRAG: "drag"}

INFO_FLAG_HW_BACKLIGHT = 0x01
INFO_FLAG_TOUCH = 0x02
INFO_FLAG_SPLASH_STORE = 0x04

CFG_FLAG_SWALLOW_WAKE = 0x01
CFG_FLAG_SPLASH_ON_UNLINK = 0x02

STATUS_NAMES = {
    0: "ok",
    1: "unknown command",
    2: "bad length",
    3: "bad parameter",
    4: "bad image",
    5: "image size mismatch",
    6: "storage error",
    7: "unsupported",
}


def encode_frame(ftype: int, seq: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<BBI", ftype, seq & 0xFF, len(payload)) + payload
    return SOF + body + struct.pack("<I", zlib.crc32(body))


class FrameParser:
    """Incremental parser: feed() bytes, iterate the complete frames."""

    def __init__(self, max_payload: int = 4096):
        self._buf = bytearray()
        self.max_payload = max_payload
        self.errors = 0

    def feed(self, data: bytes) -> Iterator[Tuple[int, int, bytes]]:
        self._buf += data
        while True:
            start = self._buf.find(SOF)
            if start < 0:
                # keep a trailing 'T' that may be the first half of a SOF
                del self._buf[: max(0, len(self._buf) - 1)]
                return
            del self._buf[:start]
            if len(self._buf) < HEADER_LEN:
                return
            ftype, seq, length = struct.unpack_from("<BBI", self._buf, 2)
            if length > self.max_payload:
                self.errors += 1
                del self._buf[:2]
                continue
            total = HEADER_LEN + length + CRC_LEN
            if len(self._buf) < total:
                return
            (crc,) = struct.unpack_from("<I", self._buf, HEADER_LEN + length)
            if crc != zlib.crc32(bytes(self._buf[2 : HEADER_LEN + length])):
                self.errors += 1
                del self._buf[:2]
                continue
            payload = bytes(self._buf[HEADER_LEN : HEADER_LEN + length])
            del self._buf[:total]
            yield ftype, seq, payload


@dataclass(frozen=True)
class DeckInfo:
    proto: Tuple[int, int]
    firmware: Tuple[int, int, int]
    rows: int
    cols: int
    key_size: Tuple[int, int]
    encoders: int
    strip_size: Tuple[int, int]
    strip_segments: int
    formats: int
    flags: int
    max_payload: int
    serial: str
    model: str

    @property
    def key_count(self) -> int:
        return self.rows * self.cols

    @property
    def segment_width(self) -> int:
        return self.strip_size[0] // self.strip_segments if self.strip_segments else 0

    @property
    def has_hw_backlight(self) -> bool:
        return bool(self.flags & INFO_FLAG_HW_BACKLIGHT)

    @classmethod
    def unpack(cls, b: bytes) -> "DeckInfo":
        if len(b) < 54:
            raise ValueError("INFO payload too short")
        (pmaj, pmin, fmaj, fmin, fpat, rows, cols, kw, kh, enc, sw, sh, seg, fmts, flags, _r, maxp) = struct.unpack_from(
            "<BBBBBBBHHBHHBBBBI", b, 0
        )
        serial = b[24:40].split(b"\0")[0].decode("ascii", "replace")
        model = b[40:54].split(b"\0")[0].decode("ascii", "replace")
        return cls((pmaj, pmin), (fmaj, fmin, fpat), rows, cols, (kw, kh), enc, (sw, sh), seg, fmts, flags, maxp, serial, model)


@dataclass(frozen=True)
class DeckState:
    awake: bool
    brightness: int
    keys: int
    encoder_buttons: int
    host_suspended: bool
    uptime_ms: int
    rx_errors: int

    @classmethod
    def unpack(cls, b: bytes) -> "DeckState":
        awake, bri, keys, btn, flags, up, err = struct.unpack_from("<BBIBBIH", b, 0)
        return cls(bool(awake), bri, keys, btn, bool(flags & 1), up, err)


@dataclass
class DeckConfig:
    default_brightness: int = 70
    sleep_timeout_s: int = 0
    long_press_ms: int = 500
    debounce_ms: int = 8
    enc_steps_per_detent: int = 4
    enc_invert_mask: int = 0
    swallow_wake: bool = True
    splash_on_unlink: bool = True

    def pack(self) -> bytes:
        flags = (CFG_FLAG_SWALLOW_WAKE if self.swallow_wake else 0) | (
            CFG_FLAG_SPLASH_ON_UNLINK if self.splash_on_unlink else 0
        )
        return struct.pack(
            "<BBHHBBBBH",
            1,
            self.default_brightness,
            self.sleep_timeout_s,
            self.long_press_ms,
            self.debounce_ms,
            self.enc_steps_per_detent,
            self.enc_invert_mask,
            flags,
            0,
        )

    @classmethod
    def unpack(cls, b: bytes) -> "DeckConfig":
        _v, bri, sleep, lp, deb, steps, inv, flags, _r = struct.unpack_from("<BBHHBBBBH", b, 0)
        return cls(bri, sleep, lp, deb, steps, inv, bool(flags & CFG_FLAG_SWALLOW_WAKE), bool(flags & CFG_FLAG_SPLASH_ON_UNLINK))


@dataclass(frozen=True)
class KeyEvent:
    key: int
    pressed: bool


@dataclass(frozen=True)
class EncoderRotateEvent:
    encoder: int
    delta: int


@dataclass(frozen=True)
class EncoderButtonEvent:
    encoder: int
    pressed: bool


@dataclass(frozen=True)
class TouchEvent:
    kind: str  # "tap", "long" or "drag"
    x: int
    y: int
    x2: int
    y2: int


@dataclass(frozen=True)
class PowerEvent:
    awake: bool


def decode_event(ftype: int, p: bytes) -> Optional[object]:
    if ftype == EVT_KEY and len(p) >= 2:
        return KeyEvent(p[0], bool(p[1]))
    if ftype == EVT_ENC_ROTATE and len(p) >= 2:
        return EncoderRotateEvent(p[0], struct.unpack("<b", p[1:2])[0])
    if ftype == EVT_ENC_BUTTON and len(p) >= 2:
        return EncoderButtonEvent(p[0], bool(p[1]))
    if ftype == EVT_TOUCH and len(p) >= 9:
        kind, x, y, x2, y2 = struct.unpack_from("<BHHHH", p, 0)
        return TouchEvent(TOUCH_KINDS.get(kind, str(kind)), x, y, x2, y2)
    if ftype == EVT_POWER and len(p) >= 1:
        return PowerEvent(bool(p[0]))
    return None
