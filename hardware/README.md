# Hardware, rev A

![front view with the cover removed](out/front-layout.png)

## The idea

Fifteen little LCDs are expensive and hard to buy. One 5 inch panel is neither.
Rev A puts a single 800 x 480 touch display under everything:

- A thin PCB with fifteen square windows lies directly on the display glass.
  In each window sits a clear cap, a light pipe that carries the 96 x 96 pixel
  image underneath it up to the surface. Two wings on each cap rest on two
  small tact switches soldered next to the window, so the cap has real travel
  and a real click. The display's touch layer is not used for the keys.
- The bottom 100 pixel rows of the same panel stay uncovered and touchable:
  that is the touch strip.
- Four rotary encoders sit below the display on a second PCB, each centred
  under one quarter of the strip.

Both PCBs carry the same circuit: an I2C port expander with pull-ups. They
plug into the display module's I2C header, so the only "main board" is the
display module, bought assembled.

| | |
|---|---|
| Size | 133.5 x 125.3 x 24.8 mm without caps and knobs |
| Display module | Waveshare ESP32-S3-Touch-LCD-5, 800 x 480 (ESP32-S3-WROOM-1-N16R8, GT911 touch) |
| Keys | 15, 13.0 mm clear caps on a 21.6 x 16.7 mm grid, 0.25 mm travel |
| Dials | 4 x EC11 with push switch |
| Strip | 108 x 13.5 mm, 800 x 100 px |

## Files

Everything is generated from `mech/geometry.py`, the single source of every
dimension, which is itself checked against the firmware's pixel layout.

| path | what |
|---|---|
| `mech/geometry.py` | dimensions and clearance checks |
| `mech/make_drawings.py` | writes the PCB outlines (DXF), placement files and the front view |
| `case/case.py` | CadQuery model of cover, tray and keycap; exports STEP and STL and runs interference checks |
| `electrical/netlist.py` | the two circuits as data; writes netlists and the BOM, checks them against the firmware pin map |
| `out/*.step`, `out/*.stl` | cover, tray, keycap, and `assembly.step` with the display envelope |
| `out/key-frame-pcb.dxf`, `out/encoder-pcb.dxf` | board outlines, window cut-outs, part bodies, keep-outs (layers `OUTLINE`, `CUTOUT`, `PARTS`, `KEEPOUT`, `NOTES`) |
| `out/*-placement.csv` | reference designator, part, x, y (mm from the board's top-left corner, y down), rotation |
| `out/*-netlist.csv` | net, reference, pin |
| `out/bom.csv` | everything for one deck |

Regenerate with:

```sh
pip install cadquery ezdxf pillow
python3 hardware/mech/make_drawings.py
python3 hardware/electrical/netlist.py
python3 hardware/case/case.py
```

## Circuit

```
display module I2C header (3V3, GND, SDA = GPIO8, SCL = GPIO9)
        |  4-wire cable
   J1 [ key frame PCB ] J2 ---- Qwiic cable ---- J3 [ encoder PCB ] J4 (spare)

key frame:  U1 TCA9539 at 0x74   P00-P07 = keys 0-7, P10-P16 = keys 8-14
            each key: two switches in parallel to GND, 10 k pull-up to 3V3
encoders:   U2 TCA9539 at 0x75   P00/P01 = dial 0 A/B ... P06/P07 = dial 3 A/B
            P10-P13 = push 0-3; A and B have 10 k pull-ups and 10 nF to GND
```

The firmware polls both expanders (encoders at 1 kHz, keys at 250 Hz), so the
INT pins are left unconnected. The full pin-level netlist is in `out/`.

The addresses 0x74 and 0x75 are clear of everything on the display module's
bus (CH422G at 0x20-0x27 and 0x30-0x3F, GT911 at 0x5D or 0x14, RTC at 0x51).

## What is finished and what is not

Finished: dimensions, 3D parts with clearance checks, board outlines, part
placement, netlists, BOM.

**Not finished: the PCBs are not routed.** There are no copper layers or
Gerbers yet. Both boards are two-layer and simple (the key frame is 30
switches, one 24-pin chip and four resistor arrays), so this is an evening in
any PCB tool: import the DXF as the board outline, place parts from the
placement CSV, wire to the netlist. In EasyEDA Pro, *File > Import > DXF* onto
the board-outline layer does the first step; KiCad has the same under
*File > Import > Graphics*. Routed boards are the most useful contribution
this project can receive.

Check on real parts before ordering:

1. **Active-area position.** `geometry.py` assumes the 108.0 x 64.8 mm pixel
   area is centred in the display's viewing window (marked `ASSUMED`). Flash
   the firmware on a bare module first: the splash draws the fifteen key
   outlines, so a ruler tells you whether the offsets are right. An error
   shifts every window, so fix it in `geometry.py` and regenerate.
2. **Footprints.** Take the PTS810 and EC11 land patterns from the
   manufacturers' drawings, not from the body rectangles in the DXF.
3. **I2C header.** Confirm the pin order of the module's I2C connector
   against its silkscreen before making the first cable, and measure its
   supply pin: it must be 3.3 V. The vendor schematic shows a selectable
   `I2C_VCC`; at 5 V the expanders would not read the 3.3 V bus reliably.
4. **Nothing here has been built yet.** The case has not been printed and the
   switch stack-up (cap wing on plunger, 0.25 mm travel, 0.5 mm gap between
   light pipe and glass) is checked in CAD only.

## Making the parts

- **Keycaps** must be optically clear. SLA-print in clear resin and polish
  the top and bottom faces, or mill them from cast acrylic. A light frosting
  on the top face hides fingerprints at a small cost in sharpness.
- **Cover and tray** print in PETG or ASA at 0.2 mm, cover top-face down, no
  supports needed for the tray. The cover's posts press the key frame PCB onto
  the glass; do not thin them.
- **Key frame PCB**: 1.0 mm thick, black solder mask (it is visible between
  the keys), components on the top side only, since the bottom lies on glass.

## Assembly order

1. Solder both PCBs. Test them on the bench with a cable before closing
   anything: `tessera demo` lights each key and counts each dial.
2. Press four M3 heat-set inserts into the tray's corner posts.
3. Screw the display module to the tray's four posts through the floor
   (M2.5 x 6 into the module's own standoffs).
4. Fit encoders through the cover's dial holes from below and fasten with
   their nuts; this holds the encoder PCB.
5. Lay the key frame PCB on the glass, cable routed through the channel at the
   top right. Drop the fifteen caps into their windows.
6. Lower the cover over the caps, four M3 screws, knobs on.

## Licence

The hardware design files in this directory are licensed under
CERN-OHL-P-2.0 (see `LICENSE`). The display module is a commercial product of
Waveshare and is not covered.
