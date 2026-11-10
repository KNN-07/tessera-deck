"""High-level client for one Tessera Deck."""
from __future__ import annotations

import queue
import struct
import threading
from typing import Callable, List, Optional, Tuple

from . import images, protocol as P
from .transport import Transport, open_transport

Color = Tuple[int, int, int]


class DeckError(Exception):
    """The deck rejected a command."""

    def __init__(self, status: int):
        super().__init__(P.STATUS_NAMES.get(status, f"status {status}"))
        self.status = status


class DeckTimeout(Exception):
    """The deck did not answer."""


class Deck:
    """Blocking client. Requests are answered synchronously; input events arrive on a
    background thread and are delivered to `read_event()` and to registered callbacks.

        with Deck.open() as deck:
            deck.set_brightness(80)
            deck.set_key_image(0, "icon.png")
            for event in deck.events():
                print(event)
    """

    def __init__(self, transport: Transport, timeout: float = 2.0, retries: int = 1):
        self._t = transport
        self._timeout = timeout
        self._retries = retries
        self._parser = P.FrameParser()
        self._seq = 0
        self._lock = threading.Lock()  # one request at a time
        self._cv = threading.Condition()
        self._responses = {}
        self._events: "queue.Queue[object]" = queue.Queue()
        self._callbacks: List[Callable[[object], None]] = []
        self._closed = False
        self.link_error: Optional[BaseException] = None
        self._reader = threading.Thread(target=self._read_loop, name="tessera-reader", daemon=True)
        self._reader.start()
        try:
            self.info = P.DeckInfo.unpack(self._request(P.CMD_GET_INFO, b"", P.RSP_INFO))
        except BaseException:
            self.close()
            raise
        if self.info.proto[0] != P.PROTO_MAJOR:
            self.close()
            raise RuntimeError(f"unsupported protocol version {self.info.proto[0]}.{self.info.proto[1]}")

    @classmethod
    def open(cls, url: Optional[str] = None, **kwargs) -> "Deck":
        """url: None to auto-detect, a serial port name, or tcp://host:port for the simulator."""
        return cls(open_transport(url), **kwargs)

    def __enter__(self) -> "Deck":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._t.close()
        with self._cv:
            self._cv.notify_all()
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=1)

    # ---- plumbing ----

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                data = self._t.read()
                if not data:
                    continue
                for ftype, seq, payload in self._parser.feed(data):
                    if ftype >= 0xA0:
                        event = P.decode_event(ftype, payload)
                        if event is not None:
                            self._events.put(event)
                            for cb in list(self._callbacks):
                                cb(event)
                    else:
                        with self._cv:
                            self._responses[seq] = (ftype, payload)
                            self._cv.notify_all()
        except BaseException as e:  # port unplugged, socket closed, ...
            if not self._closed:
                self.link_error = e
        finally:
            with self._cv:
                self._cv.notify_all()
            self._events.put(None)

    def _request(self, cmd: int, payload: bytes, expect: int = P.RSP_ACK) -> bytes:
        with self._lock:
            last: Optional[BaseException] = None
            for _ in range(self._retries + 1):
                self._seq = (self._seq + 1) & 0xFF
                seq = self._seq
                with self._cv:
                    self._responses.pop(seq, None)
                self._t.write(P.encode_frame(cmd, seq, payload))
                with self._cv:
                    ok = self._cv.wait_for(
                        lambda: seq in self._responses or self._closed or self.link_error is not None, self._timeout
                    )
                    resp = self._responses.pop(seq, None)
                if resp is None:
                    if self.link_error is not None:
                        raise ConnectionError("link to the deck was lost") from self.link_error
                    if self._closed:
                        raise ConnectionError("deck is closed")
                    last = DeckTimeout(f"no answer to command 0x{cmd:02x}")
                    continue
                ftype, data = resp
                if ftype == P.RSP_ACK and data and data[0] != 0:
                    raise DeckError(data[0])
                if ftype != expect:
                    raise RuntimeError(f"unexpected response 0x{ftype:02x} to command 0x{cmd:02x}")
                return data
            assert last is not None
            raise last

    # ---- events ----

    def read_event(self, timeout: Optional[float] = None) -> Optional[object]:
        """Next input event, or None on timeout or when the link is gone."""
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def events(self):
        """Iterates input events until the deck is closed or unplugged."""
        while True:
            event = self._events.get()
            if event is None:
                return
            yield event

    def on_event(self, callback: Callable[[object], None]) -> None:
        """Calls `callback(event)` on the reader thread for every input event."""
        self._callbacks.append(callback)

    # ---- queries ----

    def ping(self, data: bytes = b"") -> bytes:
        return self._request(P.CMD_PING, data)[1:]

    def get_state(self) -> P.DeckState:
        return P.DeckState.unpack(self._request(P.CMD_GET_STATE, b"", P.RSP_STATE))

    def get_config(self) -> P.DeckConfig:
        return P.DeckConfig.unpack(self._request(P.CMD_GET_CONFIG, b"", P.RSP_CONFIG))

    def set_config(self, config: P.DeckConfig) -> None:
        """Stores the configuration in the deck's flash."""
        self._request(P.CMD_SET_CONFIG, config.pack())

    # ---- display ----

    def set_brightness(self, percent: int) -> None:
        self._request(P.CMD_SET_BRIGHTNESS, bytes([percent]))

    def set_power(self, awake: bool) -> None:
        self._request(P.CMD_SET_POWER, bytes([1 if awake else 0]))

    def set_key_image(self, key: int, image: Optional[images.ImageLike], fmt: str = "jpeg", quality: int = 92) -> None:
        """Shows an image on a key (resized to the key size). None clears the key."""
        if image is None:
            self.fill_key(key, (0, 0, 0))
            return
        if fmt == "jpeg":
            self.set_key_data(key, P.FMT_JPEG, images.to_jpeg(image, self.info.key_size, quality))
        elif fmt == "rgb565":
            self.set_key_data(key, P.FMT_RGB565, images.to_rgb565(image, self.info.key_size))
        else:
            raise ValueError("fmt must be 'jpeg' or 'rgb565'")

    def set_key_data(self, key: int, fmt: int, data: bytes) -> None:
        """Sends already-encoded image data: exactly key-sized baseline JPEG or RGB565-LE."""
        self._request(P.CMD_SET_KEY_IMAGE, bytes([key, fmt]) + data)

    def set_strip_image(self, image: images.ImageLike, x: int = 0, y: int = 0,
                        size: Optional[Tuple[int, int]] = None, fmt: str = "jpeg", quality: int = 92) -> None:
        """Draws an image on the touch strip at (x, y). `size` defaults to the image's own size."""
        im = images.load(image)
        w, h = size or im.size
        data = images.to_jpeg(im, (w, h), quality) if fmt == "jpeg" else images.to_rgb565(im, (w, h))
        self.set_strip_data(x, y, w, h, P.FMT_JPEG if fmt == "jpeg" else P.FMT_RGB565, data)

    def set_strip_data(self, x: int, y: int, w: int, h: int, fmt: int, data: bytes) -> None:
        self._request(P.CMD_SET_STRIP_IMAGE, struct.pack("<HHHHBB", x, y, w, h, fmt, 0) + data)

    def set_segment_image(self, segment: int, image: images.ImageLike, **kwargs) -> None:
        """Fills one strip segment (the area above one encoder)."""
        sw = self.info.segment_width
        self.set_strip_image(image, x=segment * sw, y=0, size=(sw, self.info.strip_size[1]), **kwargs)

    def _fill(self, target: int, index: int, color: Color) -> None:
        self._request(P.CMD_FILL, struct.pack("<BBH", target, index, images.rgb565(color)))

    def fill_key(self, key: int, color: Color) -> None:
        self._fill(P.FILL_KEY, key, color)

    def fill_strip(self, color: Color) -> None:
        self._fill(P.FILL_STRIP, 0, color)

    def fill_keys(self, color: Color) -> None:
        self._fill(P.FILL_ALL_KEYS, 0, color)

    def fill_all(self, color: Color) -> None:
        self._fill(P.FILL_ALL, 0, color)

    def reset(self) -> None:
        """Back to the splash screen and the default brightness."""
        self._request(P.CMD_RESET_DISPLAY, b"")

    def save_splash(self) -> None:
        """Makes whatever is on screen now the boot splash."""
        self._request(P.CMD_SAVE_SPLASH, b"")

    def clear_splash(self) -> None:
        self._request(P.CMD_CLEAR_SPLASH, b"")

    def reboot(self, bootloader: bool = False) -> None:
        self._request(P.CMD_REBOOT, bytes([1 if bootloader else 0]))
