"""tessera - command-line tool for bring-up, testing and scripting."""
from __future__ import annotations

import argparse
import colorsys
import dataclasses
import sys
import time

from PIL import Image, ImageDraw

from . import Deck, DeckError, DeckTimeout, find_serial_ports
from .protocol import EncoderButtonEvent, EncoderRotateEvent, KeyEvent, TouchEvent


def _color(text: str):
    text = text.lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError("colour must be RRGGBB")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def _key_tile(size, label: str, hue: float, lit: bool = False) -> Image.Image:
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 1.0 if lit else 0.45)
    im = Image.new("RGB", size, (int(r * 255), int(g * 255), int(b * 255)))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(255, 255, 255) if lit else (20, 20, 20), width=3)
    box = d.textbbox((0, 0), label, font_size=size[1] // 2)
    d.text(((size[0] - box[2] - box[0]) / 2, (size[1] - box[3] - box[1]) / 2), label, fill=(255, 255, 255),
           font_size=size[1] // 2)
    return im


def _segment(size, index: int, value: int, pressed: bool = False) -> Image.Image:
    im = Image.new("RGB", size, (60, 60, 70) if pressed else (18, 20, 26))
    d = ImageDraw.Draw(im)
    d.text((12, 8), f"Dial {index + 1}", fill=(200, 205, 210), font_size=20)
    d.text((12, 36), str(value), fill=(255, 255, 255), font_size=34)
    w = size[0] - 24
    d.rectangle([12, size[1] - 16, 12 + w, size[1] - 8], fill=(50, 54, 62))
    d.rectangle([12, size[1] - 16, 12 + w * max(0, min(100, value)) // 100, size[1] - 8], fill=(40, 190, 170))
    return im


def cmd_demo(deck: Deck, args) -> None:
    """Paints every key and segment, then mirrors input until Ctrl-C."""
    info = deck.info
    values = [50] * info.encoders
    deck.set_brightness(args.brightness)
    for k in range(info.key_count):
        deck.set_key_image(k, _key_tile(info.key_size, str(k), k / max(1, info.key_count)))
    seg_size = (info.segment_width, info.strip_size[1])
    for i in range(info.encoders):
        deck.set_segment_image(i, _segment(seg_size, i, values[i]))
    print("Demo running: press keys, turn dials, touch the strip. Ctrl-C to stop.")
    try:
        for ev in deck.events():
            print(ev)
            if isinstance(ev, KeyEvent):
                deck.set_key_image(ev.key, _key_tile(info.key_size, str(ev.key), ev.key / info.key_count, ev.pressed))
            elif isinstance(ev, EncoderRotateEvent):
                values[ev.encoder] = max(0, min(100, values[ev.encoder] + 2 * ev.delta))
                deck.set_segment_image(ev.encoder, _segment(seg_size, ev.encoder, values[ev.encoder]))
            elif isinstance(ev, EncoderButtonEvent):
                deck.set_segment_image(ev.encoder, _segment(seg_size, ev.encoder, values[ev.encoder], ev.pressed))
            elif isinstance(ev, TouchEvent) and info.segment_width:
                seg = min(info.strip_segments - 1, ev.x // info.segment_width)
                if ev.kind == "tap":
                    values[seg] = 100 * (ev.x - seg * info.segment_width) // info.segment_width
                elif ev.kind == "long":
                    values[seg] = 50
                deck.set_segment_image(seg, _segment(seg_size, seg, values[seg]))
    except KeyboardInterrupt:
        pass
    deck.reset()


def cmd_bench(deck: Deck, args) -> None:
    """Measures how long a full page of key images takes."""
    info = deck.info
    tiles = [_key_tile(info.key_size, str(k), k / info.key_count) for k in range(info.key_count)]
    for fmt in ("jpeg", "rgb565"):
        start = time.perf_counter()
        for k, tile in enumerate(tiles):
            deck.set_key_image(k, tile, fmt=fmt)
        ms = (time.perf_counter() - start) * 1000
        print(f"{fmt:7s} {info.key_count} keys in {ms:6.0f} ms  ({ms / info.key_count:.1f} ms per key)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tessera", description=__doc__)
    ap.add_argument("-d", "--device", help="serial port (e.g. /dev/ttyACM0, COM5) or tcp://host:port; default: auto-detect")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list connected decks")
    sub.add_parser("info", help="show device information")
    sub.add_parser("state", help="show live state")
    p = sub.add_parser("brightness", help="set brightness")
    p.add_argument("percent", type=int)
    p = sub.add_parser("image", help="show an image file on a key")
    p.add_argument("key", type=int)
    p.add_argument("file")
    p.add_argument("--raw", action="store_true", help="send uncompressed RGB565 instead of JPEG")
    p = sub.add_parser("strip", help="show an image file on the touch strip")
    p.add_argument("file")
    p.add_argument("--segment", type=int, help="fill one segment instead of the whole strip")
    p = sub.add_parser("fill", help="fill with a colour")
    p.add_argument("target", choices=["key", "keys", "strip", "all"])
    p.add_argument("color", type=_color, help="RRGGBB")
    p.add_argument("--key", type=int, default=0)
    sub.add_parser("reset", help="show the splash screen")
    sub.add_parser("sleep", help="blank the display")
    sub.add_parser("wake", help="wake the display")
    sub.add_parser("monitor", help="print input events")
    p = sub.add_parser("demo", help="interactive hardware test")
    p.add_argument("--brightness", type=int, default=80)
    sub.add_parser("bench", help="measure image transfer speed")
    p = sub.add_parser("config", help="show or change the stored configuration")
    p.add_argument("settings", nargs="*", metavar="name=value",
                   help="e.g. default_brightness=60 sleep_timeout_s=600 enc_invert_mask=5")
    p = sub.add_parser("splash", help="store or remove a custom boot splash")
    p.add_argument("action", choices=["save", "clear"])
    sub.add_parser("bootloader", help="reboot into the firmware-update bootloader")
    sub.add_parser("reboot", help="restart the deck")

    args = ap.parse_args(argv)
    if args.cmd == "list":
        ports = find_serial_ports()
        print("\n".join(ports) if ports else "no deck found")
        return 0

    try:
        with Deck.open(args.device) as deck:
            info = deck.info
            if args.cmd == "info":
                for field in dataclasses.fields(info):
                    print(f"{field.name:15s} {getattr(info, field.name)}")
            elif args.cmd == "state":
                print(deck.get_state())
            elif args.cmd == "brightness":
                deck.set_brightness(args.percent)
            elif args.cmd == "image":
                deck.set_key_image(args.key, args.file, fmt="rgb565" if args.raw else "jpeg")
            elif args.cmd == "strip":
                if args.segment is None:
                    deck.set_strip_image(args.file, size=info.strip_size)
                else:
                    deck.set_segment_image(args.segment, args.file)
            elif args.cmd == "fill":
                {"key": lambda: deck.fill_key(args.key, args.color), "keys": lambda: deck.fill_keys(args.color),
                 "strip": lambda: deck.fill_strip(args.color), "all": lambda: deck.fill_all(args.color)}[args.target]()
            elif args.cmd == "reset":
                deck.reset()
            elif args.cmd in ("sleep", "wake"):
                deck.set_power(args.cmd == "wake")
            elif args.cmd == "monitor":
                try:
                    for ev in deck.events():
                        print(ev, flush=True)
                except KeyboardInterrupt:
                    pass
            elif args.cmd == "demo":
                cmd_demo(deck, args)
            elif args.cmd == "bench":
                cmd_bench(deck, args)
            elif args.cmd == "config":
                cfg = deck.get_config()
                for item in args.settings:
                    name, _, value = item.partition("=")
                    if not hasattr(cfg, name):
                        ap.error(f"unknown setting {name!r}")
                    current = getattr(cfg, name)
                    setattr(cfg, name, value.lower() in ("1", "true", "yes", "on") if isinstance(current, bool) else int(value, 0))
                if args.settings:
                    deck.set_config(cfg)
                for field in dataclasses.fields(cfg):
                    print(f"{field.name:22s} {getattr(cfg, field.name)}")
            elif args.cmd == "splash":
                deck.save_splash() if args.action == "save" else deck.clear_splash()
            elif args.cmd in ("bootloader", "reboot"):
                deck.reboot(bootloader=args.cmd == "bootloader")
    except (DeckError, DeckTimeout, OSError) as e:
        print(f"tessera: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
