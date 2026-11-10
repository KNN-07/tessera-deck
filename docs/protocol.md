# Tessera Deck Protocol (TDP) v1.0

This is the normative description of how a host talks to a Tessera Deck. The
constants live in `firmware/core/td_proto.h`; the firmware core
(`firmware/core/td_device.c`), the Python library and the OpenDeck plugin all
implement this document.

## Transport

A reliable byte stream:

- **Real hardware:** one USB CDC-ACM interface (a virtual serial port). The
  baud rate is ignored. The host must assert **DTR** while it has the port
  open: the deck only sends input events while DTR is set, and treats a falling
  DTR as "host gone" (see [Link state](#link-state)).
- **Simulator:** TCP on `127.0.0.1:47800`. A TCP connection counts as DTR.

All multi-byte integers are little-endian.

## Frame

```
offset  size  field
0       1     0x54 'T'
1       1     0x44 'D'
2       1     type
3       1     seq
4       4     len      payload length in bytes
8       len   payload
8+len   4     crc32    IEEE CRC-32 (zlib's crc32) over type, seq, len and payload
```

Receiver rules:

- Bytes outside a frame are skipped until `'T' 'D'` is seen.
- A frame whose `len` exceeds the receiver's limit, or whose CRC is wrong, is
  dropped without an answer. The deck counts such frames (`STATE.rx_errors`).
- A frame that stalls for more than 1000 ms between two reads is dropped.
- The deck's payload limit is reported in `INFO.max_payload` (160 010 bytes on
  rev A: a whole-strip RGB565 image plus its header).

## Request and response

The host sends a request with any `seq`; the deck answers every request with
exactly one frame carrying the same `seq`. Requests are processed strictly in
order. A host should send one request at a time and wait for the answer (2 s is
a generous timeout; a full-strip JPEG takes well under 100 ms).

Most requests are answered with `ACK` (0x80), whose payload is one status byte:

| status | name | meaning |
|---|---|---|
| 0 | OK | done |
| 1 | UNKNOWN_CMD | request type not known to this firmware |
| 2 | BAD_LENGTH | payload length wrong for this request |
| 3 | BAD_PARAM | a field is out of range |
| 4 | BAD_IMAGE | image data could not be decoded |
| 5 | SIZE_MISMATCH | image is not exactly the size of its target |
| 6 | STORAGE | flash read or write failed |
| 7 | UNSUPPORTED | this hardware cannot do it |

### Requests

| type | name | payload | answer |
|---|---|---|---|
| 0x01 | GET_INFO | none | INFO (0x81) |
| 0x02 | PING | 0 to 256 arbitrary bytes | ACK: status, then the same bytes |
| 0x03 | GET_STATE | none | STATE (0x82) |
| 0x04 | GET_CONFIG | none | CONFIG (0x83) |
| 0x05 | SET_CONFIG | 12-byte config (below) | ACK; stored in flash |
| 0x10 | SET_BRIGHTNESS | `percent` u8, 0 to 100 | ACK |
| 0x11 | SET_POWER | `awake` u8, 0 or 1 | ACK, then a POWER event if the state changed |
| 0x12 | SET_KEY_IMAGE | `key` u8, `format` u8, image data | ACK |
| 0x13 | SET_STRIP_IMAGE | `x` u16, `y` u16, `w` u16, `h` u16, `format` u8, reserved u8 (0), image data | ACK |
| 0x14 | FILL | `target` u8, `index` u8, `color` u16 RGB565 | ACK |
| 0x15 | RESET_DISPLAY | none | ACK; shows the splash, default brightness, wakes |
| 0x16 | SAVE_SPLASH | none | ACK; the current screen becomes the boot splash |
| 0x17 | CLEAR_SPLASH | none | ACK; back to the built-in splash |
| 0x20 | REBOOT | `mode` u8: 0 normal, 1 firmware-update bootloader | ACK, then the deck restarts |

**Keys** are numbered row by row from the top-left: key `k` is in row
`k / cols`, column `k % cols`.

**Image formats.** `0` = raw RGB565, little-endian, row by row, exactly
`w * h * 2` bytes. `1` = baseline JPEG (no progressive, no arithmetic coding)
whose pixel size is exactly the target's. A key image is always
`key_w x key_h`. A strip image may cover any rectangle inside the strip;
`x`, `y` are relative to the strip's top-left corner. The host does all
scaling. `INFO.formats` says which formats a deck accepts.

**FILL targets.** `0` one key (`index` = key), `1` the strip, `2` all keys,
`3` the whole panel including the gaps between keys.

### INFO (0x81), 54 bytes

| offset | size | field |
|---|---|---|
| 0 | 2 | protocol version: major, minor |
| 2 | 3 | firmware version: major, minor, patch |
| 5 | 2 | key rows, key columns |
| 7 | 4 | key width u16, key height u16, in pixels |
| 11 | 1 | number of encoders |
| 12 | 4 | strip width u16, strip height u16 (0 x 0 = no strip) |
| 16 | 1 | strip segments (one above each encoder) |
| 17 | 1 | accepted image formats: bit 0 RGB565, bit 1 JPEG |
| 18 | 1 | flags: bit 0 hardware backlight dimming, bit 1 touch, bit 2 splash storage |
| 19 | 1 | reserved, 0 |
| 20 | 4 | largest accepted payload, u32 |
| 24 | 16 | serial number, ASCII, zero-padded |
| 40 | 14 | model name, ASCII, zero-padded (`tessera-a5` for rev A) |

A host must refuse to talk to a deck whose protocol **major** differs from its
own. A higher **minor** only adds things: unknown requests are answered
`UNKNOWN_CMD`, and hosts must ignore event types and trailing bytes they do not
know.

### STATE (0x82), 14 bytes

| offset | size | field |
|---|---|---|
| 0 | 1 | awake (0 = display blanked) |
| 1 | 1 | brightness, percent |
| 2 | 4 | keys held, u32 bitmap, bit n = key n |
| 6 | 1 | encoder buttons held, bitmap |
| 7 | 1 | host suspended (USB suspend seen) |
| 8 | 4 | uptime, ms, u32 |
| 12 | 2 | rx_errors: frames dropped since boot, saturating u16 |

### CONFIG (0x83, and the SET_CONFIG payload), 12 bytes

| offset | size | field | default | allowed |
|---|---|---|---|---|
| 0 | 1 | config version | 1 | 1 |
| 1 | 1 | brightness at power-up, percent | 70 | 0 to 100 |
| 2 | 2 | sleep after this many idle seconds, u16 | 0 | 0 = never |
| 4 | 2 | touch long-press threshold, ms, u16 | 500 | 100 to 5000 |
| 6 | 1 | debounce, ms | 8 | 1 to 50 |
| 7 | 1 | encoder quadrature steps per detent | 4 | 1, 2, 4 |
| 8 | 1 | encoder direction invert, bitmap | 0 | |
| 9 | 1 | flags (below) | 3 | |
| 10 | 2 | reserved, 0 | | |

Flags: bit 0 `SWALLOW_WAKE`: the press that wakes a sleeping deck is not
reported. Bit 1 `SPLASH_ON_UNLINK`: show the splash when the host goes away.

A SET_CONFIG with any value out of range is refused with `BAD_PARAM` and
changes nothing.

## Events

Events are sent by the deck on its own. Their `seq` is a free-running counter
(a gap tells the host it missed one). Types are 0xA0 and above.

| type | name | payload |
|---|---|---|
| 0xA0 | KEY | `key` u8, `pressed` u8 |
| 0xA1 | ENC_ROTATE | `encoder` u8, `delta` i8: detents, positive = clockwise |
| 0xA2 | ENC_BUTTON | `encoder` u8, `pressed` u8 |
| 0xA3 | TOUCH | `kind` u8, `x0` u16, `y0` u16, `x1` u16, `y1` u16 |
| 0xA4 | POWER | `awake` u8 |

Keys and encoder buttons are debounced on the deck.

**Touch** covers the strip only; coordinates are relative to the strip's
top-left corner. A touch that starts outside the strip is never reported.

| kind | name | sent | coordinates |
|---|---|---|---|
| 0 | TAP | on release, if the finger moved less than 20 px and no LONG was sent | start = end |
| 1 | LONG | while still held, once the long-press threshold passes without movement | start = end |
| 2 | DRAG | on release, if the finger moved 20 px or more | start, end |

## Power

The deck is either **awake** or **asleep** (display blanked, contents kept).
It falls asleep after `sleep_timeout` seconds without input, or on
`SET_POWER 0`. Any key, dial or strip touch wakes it; with `SWALLOW_WAKE` that
whole gesture, up to the moment everything is released, is not reported.
Independently of this, the display is blanked while the USB bus is suspended
(the computer sleeps) and restored on resume.

Every change between awake and asleep is announced with a POWER event.

## Link state

When the host drops DTR or unplugs, the deck resets its frame parser and, with
`SPLASH_ON_UNLINK`, returns to the splash and the power-up brightness, so a
deck never keeps showing the icons of software that is no longer running.

The boot splash is a stored copy of the full screen. To install one, draw it
with the normal image requests (use `FILL` target 3 for the background) and
send `SAVE_SPLASH`.

## USB identity

The firmware ships with VID:PID `1209:0001`, the pid.codes **test** ID. That
is fine on your own desk and is what the host software looks for by default;
the host also accepts any port whose USB product string contains
`Tessera Deck`. Test IDs must not be used on hardware you give or sell to
others: get your own PID (pid.codes hands them out free to open-hardware
projects) and set it in `firmware/esp32s3/sdkconfig.defaults`.

The deck deliberately does **not** use Elgato's USB IDs or imitate the Stream
Deck HID protocol. Compatibility with Stream Deck plugins comes from the host
side (see `stream-deck-compatibility.md`).

## Worked example

`PING` with payload `68 69` and seq 1:

```
54 44  02  01  02 00 00 00  68 69  <crc32 of "02 01 02 00 00 00 68 69", LE>
```

answered by

```
54 44  80  01  03 00 00 00  00 68 69  <crc32>
```

`python3 -c "import zlib; print(zlib.crc32(bytes.fromhex('0201020000006869')).to_bytes(4,'little').hex())"`
prints the request's CRC bytes.
