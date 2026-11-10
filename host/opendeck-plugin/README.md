# OpenDeck plugin for the Tessera Deck

Makes a Tessera Deck appear in [OpenDeck](https://github.com/nekename/OpenDeck)
as a device with 15 keys, 4 dials and a touch strip. OpenDeck then provides
profiles, pages and every Stream Deck SDK plugin.

## Build and install

```sh
./package.sh        # builds for this machine, writes build/opendeck-tessera.plugin.zip
```

In OpenDeck: *Plugins > Install from file*, choose the zip, then restart
OpenDeck. On Linux also install the udev rule so the serial port is
accessible:

```sh
sudo cp ../40-tessera-deck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

`package.sh` builds only for the platform it runs on. The manifest also names
macOS and Windows binaries; build those on their own platforms (or by
cross-compiling) and add them to the plugin folder under the names in
`manifest.json`.

## How decks are found

Every two seconds the plugin looks for serial ports with USB ID `1209:0001`
or a product string containing `Tessera Deck`. To use the simulator or to
force a port, set `TESSERA_DEVICE` before starting OpenDeck:

```sh
TESSERA_DEVICE=tcp://127.0.0.1:47800 opendeck
```

## Mapping

| OpenDeck | deck |
|---|---|
| key image | scaled to 96 x 96, sent as JPEG |
| dial ("Encoder") image at position n | scaled to 200 x 100, drawn on strip segment n |
| brightness | `SET_BRIGHTNESS` |
| `keyDown` / `keyUp` | key events |
| `encoderChange`, `encoderDown` / `encoderUp` | dial events |
| `touchscreenPress` (segment, x 0-199, y 0-99, hold) | strip tap and long press |

Strip drags are not forwarded: OpenDeck's device API has no event for them.

## Test

`tests/test_plugin.py` starts the firmware simulator, plays OpenDeck's side of
the websocket protocol, and checks images and input in both directions. It has
not yet been exercised inside the OpenDeck application itself.

```sh
cmake -S ../../firmware -B ../../firmware/build && cmake --build ../../firmware/build
cargo build --release
pip install pytest websockets pillow && pytest tests
```
