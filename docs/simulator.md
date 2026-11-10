# Simulator

`tessera-sim` is the real firmware core compiled for a PC, with sockets in
place of USB, switches and the LCD. Everything on the host side (the Python
library, the CLI, the OpenDeck plugin) can be developed and tested against it
with no hardware.

```sh
cmake -S firmware -B firmware/build -G Ninja && cmake --build firmware/build
firmware/build/tessera-sim            # prints its two addresses, then serves
```

| option | default | meaning |
|---|---|---|
| `--port N` | 47800 | TCP port of the device's host link (speaks TDP) |
| `--ctl N` | 47801 | TCP port of the control channel |
| `--state DIR` | `sim-state` | where the stored config and splash live |
| `--virtual-time` | off | the clock only moves on `advance`; for deterministic tests |

Both ports listen on 127.0.0.1 only.

Talk to it like a deck:

```sh
tessera -d tcp://127.0.0.1:47800 info
tessera -d tcp://127.0.0.1:47800 demo
TESSERA_DEVICE=tcp://127.0.0.1:47800 opendeck      # the plugin picks it up
```

## Control channel

One text command per line; each is answered with one line starting `ok` or
`err`. It stands in for fingers and a camera.

| command | effect |
|---|---|
| `key N down` / `key N up` | press or release key N |
| `encbtn N down` / `encbtn N up` | press or release dial N |
| `enc N D` | turn dial N by D detents (negative = anticlockwise) |
| `touch down X Y`, `touch move X Y`, `touch up` | a finger on the panel, in panel pixels (the strip starts at y = 380) |
| `advance MS` | move the clock forward (needs `--virtual-time`) |
| `screenshot PATH` | write what the panel shows, after dimming, as a binary PPM |
| `backlight` | answers `ok on 70` / `ok off 70` |
| `suspend on` / `suspend off` | pretend the USB bus suspended or resumed |
| `reboots` | answers `ok COUNT LAST_MODE` |
| `quit` | exit |

`host/python/tessera/simctl.py` wraps this in a small class used by the tests:

```python
from tessera.simctl import SimControl
ctl = SimControl(port=47801)
ctl.key(3, True); ctl.key(3, False)
ctl.turn(0, 2)
ctl.screenshot("/tmp/deck.ppm")
```
