# tessera: Python library and command-line tool

```sh
pip install -e host/python        # from the repository root
```

Requires Python 3.9+, pyserial and Pillow.

## Command line

```
tessera [-d DEVICE] COMMAND
```

`DEVICE` is a serial port (`/dev/ttyACM0`, `COM5`) or `tcp://host:port` for
the simulator. Without `-d` the first connected deck is used.

| command | does |
|---|---|
| `list` | list connected decks |
| `info`, `state` | device information, live state |
| `brightness PERCENT` | set brightness |
| `image KEY FILE [--raw]` | show an image file on a key |
| `strip FILE [--segment N]` | show an image on the touch strip or one segment |
| `fill key\|keys\|strip\|all RRGGBB [--key N]` | fill with a colour |
| `reset`, `sleep`, `wake` | splash screen, blank, unblank |
| `monitor` | print input events |
| `demo` | interactive hardware test |
| `bench` | measure image transfer speed |
| `config [name=value ...]` | show or change the stored configuration |
| `splash save\|clear` | make the current screen the boot splash, or remove it |
| `bootloader`, `reboot` | restart into the firmware updater, or normally |

Settings for `config`: `default_brightness`, `sleep_timeout_s`,
`long_press_ms`, `debounce_ms`, `enc_steps_per_detent`, `enc_invert_mask`,
and the flags documented in `docs/protocol.md`.

## Library

```python
from tessera import Deck, KeyEvent, EncoderRotateEvent, TouchEvent

with Deck.open() as deck:
    print(deck.info.model, deck.info.serial)
    deck.fill_all((0, 0, 0))
    deck.set_key_image(0, "play.png")              # path, bytes or PIL image
    deck.set_segment_image(1, "volume.png")        # strip area above dial 1

    for event in deck.events():                    # ends when unplugged
        if isinstance(event, KeyEvent):
            print("key", event.key, "down" if event.pressed else "up")
        elif isinstance(event, EncoderRotateEvent):
            print("dial", event.encoder, event.delta)
        elif isinstance(event, TouchEvent):
            print("touch", event)
```

Images are resized to the target and sent as JPEG by default; pass
`fmt="rgb565"` for lossless. `deck.on_event(callback)` delivers events on the
reader thread instead of an iterator. Requests raise `DeckError` when the deck
refuses them and `DeckTimeout` when it does not answer.

## Tests

```sh
cmake -S firmware -B firmware/build && cmake --build firmware/build
pip install -e "host/python[dev]" && pytest host/python
```
