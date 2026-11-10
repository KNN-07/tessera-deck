# What "Stream Deck functionality" means here

A Stream Deck is two things: a box with LCD keys, and desktop software that
turns key presses into actions through plugins. Tessera Deck is an open design
for the box. For the software it relies on
[OpenDeck](https://github.com/nekename/OpenDeck), an open-source (GPL-3.0)
Stream Deck host for Linux, Windows and macOS that runs plugins written for
Elgato's Stream Deck SDK. `host/opendeck-plugin` makes a Tessera Deck show up
in OpenDeck as one more device, registered as SDK device type 7, the type of a
Stream Deck +, so plugins offer their key actions *and* their dial actions.

## Hardware, feature by feature

| Stream Deck feature | Elgato reference | Tessera Deck rev A |
|---|---|---|
| LCD keys | 6 (Mini), 8 (+, Neo), 15 (MK.2), 32 (XL) | 15, in 3 rows of 5 |
| Key image size | 72 px (MK.2), 96 px (XL), 120 px (+) | 96 x 96 px, about 13 mm square |
| Key feel | membrane dome under each key | two 2.5 N tact switches under each clear cap |
| Dials | 4 on the +, turn and push | 4, turn and push, 20 detents per turn |
| Touch strip | 800 x 100 px on the + | 800 x 100 px, four 200 x 100 segments, one above each dial |
| Strip gestures | tap, long press, swipe | tap, long press, drag (start and end point) |
| Brightness | backlight PWM | 0 to 100 %, by scaling pixel values (the module's backlight is on/off only) |
| Sleep / screensaver | set from the app | idle timeout stored in the deck, `SET_POWER`, and blanking on USB suspend |
| Boot logo | custom image via the app | any screen can be saved as the boot splash |
| Connection | USB 2.0, HID | USB 2.0 full speed, CDC serial, USB-C |
| Firmware update | Elgato app | `tessera bootloader`, then `idf.py flash` or esptool |

Not part of rev A, because they belong to specific Elgato models rather than
to "a Stream Deck": the Neo's info bar and two touch buttons, the + XLR and
USB-hub docks, the Pedal, and wireless use.

## Software features

These are OpenDeck's, and work with a Tessera Deck exactly as with any other
device it drives:

- Stream Deck SDK plugins (OBS, media keys, Home Assistant, Spotify, ...),
  including Windows-only plugins through Wine
- profiles, switching profile with the focused application
- pages and folders, Multi Actions, Toggle Actions
- per-key images, titles and states; dial layouts on the strip
- brightness control

What the plugin does and does not forward:

| direction | forwarded | not forwarded |
|---|---|---|
| deck to OpenDeck | key down/up, dial rotate, dial down/up, strip tap and long press (as `touchscreenPress` with the segment and 200 x 100 coordinates) | strip drags (OpenDeck's device API has no event for them, so swipe-to-change-page is not available) |
| OpenDeck to deck | key images, dial segment images, clear, brightness, re-render after the computer wakes | - |

## What it is not

- **It does not work with Elgato's own Stream Deck application.** That app
  only talks to Elgato hardware. Making it accept this deck would mean
  presenting Elgato's USB vendor ID and copying their device protocol; the
  project does not do that, by design.
- **Plugin compatibility is OpenDeck's.** Most Stream Deck SDK plugins run;
  some that depend on Elgato-app internals do not. See OpenDeck's own notes.
- "Stream Deck" and "Elgato" are trademarks of Corsair Memory, Inc. Tessera
  Deck is an independent project and is not affiliated with or endorsed by
  them.

## Without OpenDeck

The deck is useful on its own through the Python library and the `tessera`
command (see `host/python/README.md`): set images, read keys, dials and touch,
from about ten lines of code. The wire protocol is documented in
`protocol.md` for anyone who wants to write another host.
