# Building and testing

## Without hardware

```sh
# firmware core, simulator and unit tests
cmake -S firmware -B firmware/build -G Ninja
cmake --build firmware/build
firmware/build/test_core

# the same with AddressSanitizer and UBSan
cmake -S firmware -B firmware/build-asan -G Ninja -DTESSERA_SANITIZE=ON
cmake --build firmware/build-asan && firmware/build-asan/test_core

# Python library against the simulator
pip install -e "host/python[dev]"
pytest host/python

# OpenDeck plugin against the simulator and a mock OpenDeck
cargo build --release --manifest-path host/opendeck-plugin/Cargo.toml
pip install websockets
pytest host/opendeck-plugin/tests

# hardware generators and their checks
pip install cadquery ezdxf
python3 hardware/mech/make_drawings.py
python3 hardware/electrical/netlist.py
python3 hardware/case/case.py

# PCBs: re-check the committed boards (needs KiCad 7 and numpy) ...
python3 hardware/pcb/build_boards.py --check
# ... or rebuild, re-route and re-export them, about 6 minutes
python3 hardware/pcb/build_boards.py
```

`tools/make_test_fixtures.py` regenerates `firmware/tests/jpeg_fixture.h`.

## Firmware for the ESP32-S3

Needs ESP-IDF v5.5 or later.

```sh
. $IDF_PATH/export.sh
cd firmware/esp32s3
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash        # see below for the port
```

The deck talks to the computer through the ESP32-S3's native USB (GPIO19/20),
which is also a flashing port: hold BOOT while pressing RESET to enter the ROM
bootloader, or, once Tessera firmware is running, run `tessera bootloader`.
If your module revision has a separate socket marked UART, that one is for
flashing and logs only; the deck link is the socket marked USB.

First power-up shows the splash: the fifteen key outlines and the strip. Use
it to check that the windows of your key frame line up with the pixels (see
`hardware/README.md`, "Check on real parts").

Layout of the port (`firmware/esp32s3/main`):

| file | job |
|---|---|
| `board.h` | every pin, address and panel timing |
| `hal_display.c` | RGB panel, CH422G expander, backlight |
| `hal_inputs.c` | 1 kHz task: TCA9539 expanders, GT911 touch |
| `hal_usb.c` | TinyUSB CDC-ACM, DTR and suspend tracking |
| `hal_storage.c` | config in NVS, splash in its own flash partition |
| `main.c` | wires the above into the portable core's `td_port` |

Porting to another board means writing these five files; `firmware/core` has
no hardware dependencies and is the part covered by the unit tests.

## Linux permissions

```sh
sudo cp host/40-tessera-deck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## First contact

```sh
tessera list
tessera info
tessera demo        # every key lights when pressed, dials count on the strip
tessera config sleep_timeout_s=600
```
