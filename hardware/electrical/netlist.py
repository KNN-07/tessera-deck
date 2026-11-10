#!/usr/bin/env python3
"""Tessera Deck rev A - the two boards' circuits as data, plus the bill of materials.

    python3 hardware/electrical/netlist.py

Writes to hardware/out/:
    key-frame-netlist.csv, encoder-netlist.csv   net, ref, pin, pin name
    bom.csv                                      everything needed for one deck

and checks the circuit against the firmware's pin map (firmware/esp32s3/main/board.h
and hal_inputs.c) and against the placement files from ../mech/make_drawings.py, so the
three descriptions cannot drift apart.

Both boards are the same simple circuit: a TCA9539 16-bit I2C port expander whose
inputs are pulled up to 3.3 V and switched to ground. Nothing else is active.
SPDX-License-Identifier: CERN-OHL-P-2.0
"""
import csv
import pathlib
import re
import sys
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE.parent / "out"
sys.path.insert(0, str(HERE.parent / "mech"))
import geometry as g  # noqa: E402

# TCA9539PWR, TSSOP-24 (TI datasheet SCPS202)
TCA_PINS = {
    "INT": 1, "A1": 2, "RESET": 3,
    **{f"P0{i}": 4 + i for i in range(8)},
    "GND": 12,
    **{f"P1{i}": 13 + i for i in range(8)},
    "A0": 21, "SCL": 22, "SDA": 23, "VCC": 24,
}
# Qwiic / STEMMA QT, JST SH 1.0 mm 4-pin
QWIIC_PINS = {"GND": 1, "3V3": 2, "SDA": 3, "SCL": 4}


class Board:
    def __init__(self, name):
        self.name = name
        self.nets = defaultdict(list)   # net -> [(ref, pin, pin name)]
        self.parts = {}                 # ref -> part description

    def part(self, ref, description):
        self.parts[ref] = description

    def connect(self, net, ref, pin, pin_name=""):
        assert ref in self.parts, ref
        self.nets[net].append((ref, str(pin), pin_name))

    def expander(self, ref, address):
        self.part(ref, "TCA9539PWR")
        for name in ("SCL", "SDA", "GND"):
            self.connect(name, ref, TCA_PINS[name], name)
        self.connect("3V3", ref, TCA_PINS["VCC"], "VCC")
        assert address in (0x74, 0x75)
        self.connect("GND", ref, TCA_PINS["A1"], "A1")
        self.connect("3V3" if address & 1 else "GND", ref, TCA_PINS["A0"], "A0")
        self.connect(f"{ref}_INT_NC", ref, TCA_PINS["INT"], "INT (not connected: the firmware polls)")

    def qwiic(self, ref):
        self.part(ref, "JST SM04B-SRSS-TB")
        for name, pin in QWIIC_PINS.items():
            self.connect(name, ref, pin, name)

    def resistor(self, array_ref, element, net):
        """One element of a 4 x 10 k isolated array (pins 1-8, 2-7, 3-6, 4-5) to 3.3 V."""
        self.connect(net, array_ref, element + 1, f"R{element + 1}a")
        self.connect("3V3", array_ref, 8 - element, f"R{element + 1}b")

    def pin_check(self):
        """No pin on two nets; every expander pin accounted for."""
        seen = {}
        for net, pins in self.nets.items():
            for ref, pin, _ in pins:
                assert (ref, pin) not in seen, f"{self.name}: {ref}.{pin} on {seen[(ref, pin)]} and {net}"
                seen[(ref, pin)] = net
        for ref, part in self.parts.items():
            if part.startswith("TCA9539"):
                missing = [n for n, p in TCA_PINS.items() if (ref, str(p)) not in seen]
                assert not missing, f"{self.name}: {ref} pins unconnected: {missing}"
            if part.startswith("4 x 10 k"):
                assert sum(1 for (r, _p) in seen if r == ref) == 8, f"{self.name}: {ref} has unused elements"

    def write(self, path):
        with open(path, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(["net", "ref", "pin", "pin_name"])
            for net in sorted(self.nets, key=lambda n: (n not in ("3V3", "GND", "SDA", "SCL"), n)):
                for row in sorted(self.nets[net]):
                    out.writerow([net, *row])


def key_frame():
    b = Board("key frame")
    b.expander("U1", 0x74)
    b.part("C1", "100 nF 0603")
    b.connect("3V3", "C1", 1)
    b.connect("GND", "C1", 2)
    b.qwiic("J1")
    b.qwiic("J2")
    for i in range(4):
        b.part(f"RN{i + 1}", "4 x 10 k array 0603x4")

    nkeys = g.ROWS * g.COLS
    for key in range(nkeys):
        net = f"KEY{key}"
        port_pin = f"P{key // 8}{key % 8}"          # keys 0-7 on port 0, 8-14 on port 1
        b.connect(net, "U1", TCA_PINS[port_pin], port_pin)
        b.resistor(f"RN{key // 4 + 1}", key % 4, net)
        for side in "LR":                            # the two switches of a key are in parallel
            ref = f"SW{key}{side}"
            b.part(ref, "C&K PTS810 SJM 250 SMTR LFS")
            b.connect(net, ref, 1)
            b.connect("GND", ref, 2)
    b.resistor("RN4", 3, "U1_RESET")                 # the 16th resistor holds RESET high
    b.connect("U1_RESET", "U1", TCA_PINS["RESET"], "RESET")
    b.connect("3V3", "U1", TCA_PINS["P17"], "P17 (unused input, tied high)")
    return b


def encoder_board():
    b = Board("encoder board")
    b.expander("U2", 0x75)
    b.part("C10", "100 nF 0603")
    b.connect("3V3", "C10", 1)
    b.connect("GND", "C10", 2)
    b.qwiic("J3")
    b.qwiic("J4")
    for i in range(g.ENCODERS):
        enc, rn = f"ENC{i}", f"RN{5 + i}"
        b.part(enc, "Alps EC11E, with push switch")
        b.part(rn, "4 x 10 k array 0603x4")
        for element, (signal, enc_pin, port_pin) in enumerate((
            ("A", "A", f"P0{2 * i}"), ("B", "B", f"P0{2 * i + 1}"), ("SW", "S1", f"P1{i}"),
        )):
            net = f"ENC{i}_{signal}"
            b.connect(net, enc, enc_pin, signal)
            b.connect(net, "U2", TCA_PINS[port_pin], port_pin)
            b.resistor(rn, element, net)
        b.connect("GND", enc, "C", "common")
        b.connect("GND", enc, "S2", "switch return")
        b.connect("GND", enc, "MP", "mounting lugs")
        for cap, signal in ((f"C{2 + 2 * i}", "A"), (f"C{3 + 2 * i}", "B")):
            b.part(cap, "10 nF 0603")
            b.connect(f"ENC{i}_{signal}", cap, 1)
            b.connect("GND", cap, 2)
        # the fourth element of each array: RESET on the first, unused inputs on the rest
        spare = "U2_RESET" if i == 0 else f"U2_P1{3 + i}"
        b.resistor(rn, 3, spare)
        if i == 0:
            b.connect(spare, "U2", TCA_PINS["RESET"], "RESET")
        else:
            b.connect(spare, "U2", TCA_PINS[f"P1{3 + i}"], f"P1{3 + i} (unused input)")
    b.connect("3V3", "U2", TCA_PINS["P17"], "P17 (unused input, tied high)")
    return b


# ---- cross-checks ------------------------------------------------------------

def check_firmware(keys, enc):
    board_h = (REPO / "firmware" / "esp32s3" / "main" / "board.h").read_text()
    inputs_c = (REPO / "firmware" / "esp32s3" / "main" / "hal_inputs.c").read_text()

    def define(name):
        return int(re.search(rf"#define {name} (0x[0-9A-Fa-f]+)", board_h).group(1), 16)

    def address(board, ref):
        a0 = any(r == ref and n == "A0" for r, _, n in board.nets["3V3"])
        a1 = any(r == ref and n == "A1" for r, _, n in board.nets["3V3"])
        return 0x74 | (a1 << 1) | a0

    assert address(keys, "U1") == define("BOARD_TCA_KEYS_ADDR")
    assert address(enc, "U2") == define("BOARD_TCA_ENC_ADDR")

    def bit(board, ref, net):
        """Bit position of a net in the 16-bit word the firmware reads (port 0 = low byte)."""
        (name,) = [n for r, _, n in board.nets[net] if r == ref]
        return int(name[1]) * 8 + int(name[2])

    # hal_inputs.c: key n is bit n; encoder e has A on bit 2e, B on bit 2e+1, push on bit 8+e
    for key in range(g.ROWS * g.COLS):
        assert bit(keys, "U1", f"KEY{key}") == key
    assert "keys & 0x7FFF" in inputs_c
    assert "level & (1u << (2 * e)), level & (2u << (2 * e))" in inputs_c
    assert "(backlog[backlog_n - 1] >> 8) & 0x0F" in inputs_c
    for e in range(g.ENCODERS):
        assert bit(enc, "U2", f"ENC{e}_A") == 2 * e
        assert bit(enc, "U2", f"ENC{e}_B") == 2 * e + 1
        assert bit(enc, "U2", f"ENC{e}_SW") == 8 + e


def check_placement(board, filename):
    path = OUT / filename
    if not path.exists():
        print(f"  (skipped: {filename} not generated yet - run mech/make_drawings.py)")
        return
    with open(path) as f:
        placed = {row["ref"] for row in csv.DictReader(f)}
    assert placed == set(board.parts), (
        f"{board.name}: placement and netlist disagree: {sorted(placed ^ set(board.parts))}")


# ---- bill of materials -------------------------------------------------------

def bom(boards):
    counts = defaultdict(list)
    for b in boards:
        for ref, part in b.parts.items():
            counts[part].append(ref)

    def refs(part):
        r = counts.pop(part)
        return len(r), " ".join(sorted(r, key=lambda s: (re.sub(r"\d", "", s), int(re.search(r"\d+", s).group()))))

    rows = [("section", "qty", "item", "part number / spec", "refs / notes")]

    def add(section, qty, item, spec, notes=""):
        rows.append((section, qty, item, spec, notes))

    add("module", 1, "5 inch 800 x 480 capacitive touch display with ESP32-S3",
        "Waveshare ESP32-S3-Touch-LCD-5 (the 800 x 480 version, not the 1024 x 600 'B')",
        "ESP32-S3-WROOM-1-N16R8, GT911 touch, USB-C")
    n, r = refs("C&K PTS810 SJM 250 SMTR LFS")
    add("key frame PCB", n, "tact switch 4.2 x 3.2 x 2.5 mm, 2.5 N", "C&K PTS810 SJM 250 SMTR LFS", r)
    n1, r1 = refs("TCA9539PWR")
    add("both PCBs", n1, "16-bit I2C port expander, TSSOP-24", "TI TCA9539PWR", r1)
    n, r = refs("4 x 10 k array 0603x4")
    add("both PCBs", n, "resistor array 4 x 10 k, isolated, 1206 (4 x 0603)", "e.g. Yageo YC164-JR-0710KL", r)
    n, r = refs("100 nF 0603")
    add("both PCBs", n, "capacitor 100 nF 16 V X7R 0603", "any", r)
    n, r = refs("10 nF 0603")
    add("encoder PCB", n, "capacitor 10 nF 50 V X7R 0603", "any", r)
    n, r = refs("JST SM04B-SRSS-TB")
    add("both PCBs", n, "JST SH 4-pin side-entry header (Qwiic)", "JST SM04B-SRSS-TB", r)
    n, r = refs("Alps EC11E, with push switch")
    add("encoder PCB", n, "rotary encoder with push switch, 20 mm D-shaft, 20 detents / 20 pulses",
        "Alps Alpine EC11E15244G1 or Bourns PEC11R-4220F-S0024", r)
    assert not counts, f"parts missing from the BOM: {list(counts)}"

    add("PCB", 1, "key frame board, 2 layers, 1.0 mm, black solder mask",
        f"{g.FRAME_X1 - g.FRAME_X0:.1f} x {g.FRAME_Y1 - g.FRAME_Y0:.1f} mm, 15 routed windows",
        "outline: key-frame-pcb.dxf")
    add("PCB", 1, "encoder board, 2 layers, 1.6 mm", f"{g.ENC_BOARD_W:.0f} x {g.ENC_BOARD_H:.0f} mm",
        "outline: encoder-pcb.dxf")
    add("cables", 2, "Qwiic / STEMMA QT cable, JST SH to JST SH", "50 mm and 100 mm", "key frame to encoder board, spare")
    add("cables", 1, "JST SH 4-pin to bare wires", "100 mm",
        "to the display board's I2C header: check its pin order against the silkscreen")
    add("printed", g.ROWS * g.COLS, "keycap", "keycap.stl - clear resin (SLA) or machined acrylic", "polish top and bottom faces")
    add("printed", 1, "cover", "cover.stl - PETG or ASA, 0.2 mm layers", "printed top face down")
    add("printed", 1, "tray", "tray.stl - PETG or ASA, 0.2 mm layers")
    add("hardware", 4, "knob for 6 mm D-shaft, 15-20 mm diameter", "any", "")
    add("hardware", 4, "M3 heat-set insert, 4.0 mm hole, up to 5.7 mm long", "e.g. Ruthex RX-M3x5.7", "tray corner posts")
    add("hardware", 2, "M3 x 12 socket head cap screw", "ISO 4762", "rear cover screws (recessed)")
    add("hardware", 2, "M3 x 8 button head screw", "ISO 7380", "front cover screws")
    add("hardware", 4, "M2.5 x 6 pan head screw", "ISO 7045", "display board standoffs, from below")
    add("hardware", 4, "self-adhesive rubber foot, 8-10 mm", "any", "")
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    keys, enc = key_frame(), encoder_board()
    for b in (keys, enc):
        b.pin_check()
    check_firmware(keys, enc)
    check_placement(keys, "key-frame-placement.csv")
    check_placement(enc, "encoder-placement.csv")
    keys.write(OUT / "key-frame-netlist.csv")
    enc.write(OUT / "encoder-netlist.csv")
    rows = bom((keys, enc))
    with open(OUT / "bom.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    for b in (keys, enc):
        print(f"  {b.name}: {len(b.parts)} parts, {len(b.nets)} nets")
    print(f"  bom: {len(rows) - 1} lines")
    print("netlists agree with the firmware pin map and the placement files")


if __name__ == "__main__":
    main()
