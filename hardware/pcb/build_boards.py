#!/usr/bin/env python3
"""Tessera Deck rev A - builds, routes and checks the two PCBs with KiCad.

    python3 hardware/pcb/build_boards.py            # place, route, check, export
    python3 hardware/pcb/build_boards.py --check    # only re-check the committed boards

Needs KiCad 7 with its Python module (pcbnew) and footprint library, and numpy.
Routing is done by router.py, a small deterministic grid router in this directory.

Inputs, all generated from geometry.py and netlist.py:
    ../out/*-placement.csv   where each part goes
    ../out/*-netlist.csv     which pad is on which net
Outputs:
    key-frame/key-frame.kicad_pcb, encoder/encoder.kicad_pcb    the routed boards
    fab/<board>-gerbers.zip      Gerbers and drill files, ready to upload
    fab/<board>-positions.csv    pick-and-place centroids
    fab/<board>-drc.rpt          KiCad's design-rule report
    fab/<board>.png              rendered copper, for a look

`--check` is what CI runs on the committed boards: KiCad DRC must report no
violations and no unconnected pads, and every pad must be on the net the
netlist says.
SPDX-License-Identifier: CERN-OHL-P-2.0
"""
import argparse
import csv
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "out"
FAB = HERE / "fab"
sys.path.insert(0, str(HERE.parent / "mech"))
import geometry as g  # noqa: E402
sys.path.insert(0, str(HERE))
import router  # noqa: E402

FOOTPRINT_LIB = pathlib.Path(os.environ.get("KICAD_FOOTPRINTS", "/usr/share/kicad/footprints"))

# part description in the placement file -> (library, footprint, value)
FOOTPRINTS = {
    "C&K PTS810": ("Button_Switch_SMD", "SW_SPST_PTS810", "PTS810 SJM 250"),
    "TCA9539PWR": ("Package_SO", "TSSOP-24_4.4x7.8mm_P0.65mm", "TCA9539PWR"),
    "4 x 10 k array": ("Resistor_SMD", "R_Array_Convex_4x0603", "4x10k"),
    "100 nF 0603": ("Capacitor_SMD", "C_0603_1608Metric", "100n"),
    "10 nF 0603": ("Capacitor_SMD", "C_0603_1608Metric", "10n"),
    "JST SM04B-SRSS-TB": ("Connector_JST", "JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "Qwiic"),
    "Alps EC11E": ("Rotary_Encoder", "RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm", "EC11E"),
}
# The library's encoder footprint has its origin on pad A; the shaft is here.
EC11_SHAFT = (7.5, 2.5)

# Design rules: within the standard (cheapest) capability of the usual prototype fabs.
TRACK, CLEARANCE = router.TRACK, router.CLEARANCE
VIA_DIAMETER, VIA_DRILL = router.VIA_DIAMETER, router.VIA_DRILL
EDGE_CLEARANCE = 0.3

BOARDS = {
    "key-frame": dict(
        title="TESSERA KEY FRAME", thickness=g.FRAME_PCB_THICK,
        size=(g.FRAME_X1 - g.FRAME_X0, g.FRAME_Y1 - g.FRAME_Y0), corner=1.0),
    "encoder": dict(
        title="TESSERA ENCODERS", thickness=1.6,
        size=(g.ENC_BOARD_W, g.ENC_BOARD_H), corner=1.5),
}


def pt(x, y):
    return VECTOR2I(FromMM(x), FromMM(y))


# ---- building ----------------------------------------------------------------

def rounded_rect(board, x0, y0, x1, y1, r, layer=pcbnew.Edge_Cuts, width=0.1):
    k = r * (1 - math.sqrt(0.5))        # arc midpoint offset from the corner
    lines = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)),
             ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
    arcs = [((x1 - r, y0), (x1 - k, y0 + k), (x1, y0 + r)), ((x1, y1 - r), (x1 - k, y1 - k), (x1 - r, y1)),
            ((x0 + r, y1), (x0 + k, y1 - k), (x0, y1 - r)), ((x0, y0 + r), (x0 + k, y0 + k), (x0 + r, y0))]
    for a, b in lines:
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(*a))
        s.SetEnd(pt(*b))
        s.SetLayer(layer)
        s.SetWidth(FromMM(width))
        board.Add(s)
    for a, m, b in arcs:
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(pt(*a), pt(*m), pt(*b))
        s.SetLayer(layer)
        s.SetWidth(FromMM(width))
        board.Add(s)


def text(board, string, x, y, size=1.0, layer=pcbnew.F_SilkS, angle=0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(string)
    t.SetPosition(pt(x, y))
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(size * 0.15))
    t.SetTextAngleDegrees(angle)
    board.Add(t)


def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def netlist_pads(name):
    """{(ref, pad number): net} from the netlist; single-pin nets are left unconnected."""
    rows = read_csv(OUT / f"{name}-netlist.csv")
    count = {}
    for r in rows:
        count[r["net"]] = count.get(r["net"], 0) + 1
    return {(r["ref"], r["pin"]): r["net"] for r in rows if count[r["net"]] > 1}


def new_board(name):
    spec = BOARDS[name]
    w, h = spec["size"]
    board = pcbnew.BOARD()
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(2)
    ds.SetBoardThickness(FromMM(spec["thickness"]))
    ds.m_CopperEdgeClearance = FromMM(EDGE_CLEARANCE)
    ds.m_MinClearance = FromMM(CLEARANCE)
    ds.m_TrackMinWidth = FromMM(TRACK)
    ds.m_ViasMinSize = FromMM(VIA_DIAMETER)
    ds.m_MinThroughDrill = FromMM(VIA_DRILL)
    ds.m_HoleToHoleMin = FromMM(0.25)
    nc = ds.m_NetSettings.m_DefaultNetClass
    nc.SetClearance(FromMM(CLEARANCE))
    nc.SetTrackWidth(FromMM(TRACK))
    nc.SetViaDiameter(FromMM(VIA_DIAMETER))
    nc.SetViaDrill(FromMM(VIA_DRILL))

    rounded_rect(board, 0, 0, w, h, spec["corner"])
    if name == "key-frame":
        half = g.CUTOUT / 2
        for key in range(g.ROWS * g.COLS):
            cx, cy = g.key_center(key)
            cx, cy = cx - g.FRAME_X0, cy - g.FRAME_Y0
            rounded_rect(board, cx - half, cy - half, cx + half, cy + half, g.CUTOUT_RADIUS)
        for x, y in g.all_hold_down_posts():   # where the cover's posts press on the board
            c = pcbnew.PCB_SHAPE(board)
            c.SetShape(pcbnew.SHAPE_T_CIRCLE)
            c.SetCenter(pt(x - g.FRAME_X0, y - g.FRAME_Y0))
            c.SetEnd(pt(x - g.FRAME_X0 + g.HOLD_DOWN_POST_D / 2, y - g.FRAME_Y0))
            c.SetLayer(pcbnew.Cmts_User)
            c.SetWidth(FromMM(0.1))
            board.Add(c)

    nets = {}
    pads = netlist_pads(name)
    for net_name in sorted(set(pads.values())):
        net = pcbnew.NETINFO_ITEM(board, net_name)
        board.Add(net)
        nets[net_name] = net

    for row in read_csv(OUT / f"{name}-placement.csv"):
        ref, x, y, rot = row["ref"], float(row["x_mm"]), float(row["y_mm"]), float(row["rotation_deg"])
        lib, fp_name, value = next(v for k, v in FOOTPRINTS.items() if row["part"].startswith(k))
        fp = pcbnew.FootprintLoad(str(FOOTPRINT_LIB / f"{lib}.pretty"), fp_name)
        assert fp is not None, fp_name
        fp.SetFPID(pcbnew.LIB_ID(lib, fp_name))
        fp.SetReference(ref)
        fp.SetValue(value)
        if fp_name.startswith("RotaryEncoder"):
            assert rot == 0
            x, y = x - EC11_SHAFT[0], y - EC11_SHAFT[1]
        fp.SetPosition(pt(x, y))
        fp.SetOrientationDegrees(rot)
        # Designators would land on pads on boards this dense: keep them on the
        # fabrication layer (the assembly drawing) and off the silkscreen.
        fp.Reference().SetLayer(pcbnew.F_Fab)
        fp.Value().SetVisible(False)
        for pad in fp.Pads():
            net = pads.get((ref, pad.GetNumber()))
            if net:
                pad.SetNet(nets[net])
        board.Add(fp)

    used = {(fp.GetReference(), p.GetNumber()) for fp in board.GetFootprints() for p in fp.Pads()}
    missing = set(pads) - used
    assert not missing, f"{name}: netlist pins with no pad: {sorted(missing)}"

    silk(board, name)
    return board


def silk(board, name):
    spec = BOARDS[name]
    w, h = spec["size"]
    if name == "key-frame":
        text(board, f"{spec['title']} rev A", 31.0, 2.4, 1.0)
        text(board, "CERN-OHL-P-2.0", 31.0, 4.4, 0.8)
        text(board, "I2C 0x74", 31.0, 6.2, 0.8)
        for ref, label in (("J1", "IN"), ("J2", "OUT")):
            fp = board.FindFootprintByReference(ref)
            text(board, label, ToMM(fp.GetPosition().x), 8.3, 0.9)
        text(board, "top edge: GND 3V3 SDA SCL", 104.7, 9.4 - 2.6, 0.7) if False else None
    else:
        text(board, f"{spec['title']} rev A", 56.0, 19.0, 1.0)
        text(board, "0x75", 56.0, 20.6, 0.8)
        text(board, "IN", 3.3, 16.6, 0.9)
        text(board, "THRU", 108.4, 16.6, 0.9)


# ---- routing -----------------------------------------------------------------

def route(board, name):
    """Routes the board with router.py and adds the tracks and vias to it."""
    spec = BOARDS[name]
    w, h = spec["size"]
    holes = []
    if name == "key-frame":
        half = g.CUTOUT / 2
        for key in range(g.ROWS * g.COLS):
            cx, cy = g.key_center(key)
            cx, cy = cx - g.FRAME_X0, cy - g.FRAME_Y0
            holes.append((cx - half, cy - half, cx + half, cy + half))

    def load():
        r = router.Router(w, h, holes, spec["corner"])
        for fp in board.GetFootprints():
            fine_pitch = fp.GetFPID().GetLibItemName().wx_str().startswith(("TSSOP", "R_Array", "JST"))
            centre = fp.GetBoundingBox(False, False).Centre()
            rows = {}
            for pad in fp.Pads():
                box = pad.GetBoundingBox()
                escape = None
                if fine_pitch and pad.GetNumber() != "MP":      # straight out, along the pad's long axis
                    if box.GetWidth() > box.GetHeight():
                        escape = (1 if pad.GetPosition().x > centre.x else -1, 0)
                        across = pad.GetPosition().y
                    else:
                        escape = (0, 1 if pad.GetPosition().y > centre.y else -1)
                        across = pad.GetPosition().x
                    rows.setdefault(escape, []).append((across, pad.GetNumber()))
            far = {(e, number) for e, row in rows.items() for i, (_, number) in enumerate(sorted(row)) if i % 2}
            for pad in fp.Pads():
                box = pad.GetBoundingBox()
                escape = next((e for e, row in rows.items() if any(n == pad.GetNumber() for _, n in row)), None)
                layers = tuple(i for i, layer in enumerate((pcbnew.F_Cu, pcbnew.B_Cu)) if pad.IsOnLayer(layer))
                r.add_pad(fp.GetReference(), pad.GetNumber(), pad.GetNetname(),
                          ToMM(pad.GetPosition().x), ToMM(pad.GetPosition().y),
                          ToMM(box.GetLeft()), ToMM(box.GetTop()), ToMM(box.GetRight()), ToMM(box.GetBottom()),
                          layers, pad.GetShape() == pcbnew.PAD_SHAPE_CIRCLE, escape, (escape, pad.GetNumber()) in far)
        return r

    def span(r, net):
        pads = [p for p in r.pads if p.net == net]
        return (max(p.cx for p in pads) - min(p.cx for p in pads)) + (max(p.cy for p in pads) - min(p.cy for p in pads))

    probe = load()
    signals = sorted((n for n in probe.net_ids if n not in ("GND", "3V3")), key=lambda n: (span(probe, n), n))
    # The router is greedy, so the order of the nets matters: shortest first, the two
    # big ones last. Nets that still do not fit go to the front of the queue and the
    # board is routed again from scratch.
    order = signals + ["3V3", "GND"]
    for attempt in range(1, 9):
        r = load()
        r.verbose = bool(os.environ.get("ROUTER_VERBOSE"))
        if r.route(order, max_rips=0):
            break
        failed = [n for n, _ in r.failed]
        print(f"    attempt {attempt}: {', '.join(failed)} did not fit; routing again with them first", flush=True)
        order = failed + [n for n in order if n not in failed]
    else:
        sys.exit(f"{name}: could not route {[n for n, _ in r.failed]}")
    print(f"    routed {len(r.net_ids)} nets on attempt {attempt}", flush=True)

    nets = board.GetNetsByName()
    for net, layer, x0, y0, x1, y1 in r.segments:
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(x0, y0))
        t.SetEnd(pt(x1, y1))
        t.SetWidth(FromMM(router.TRACK))
        t.SetLayer(pcbnew.F_Cu if layer == 0 else pcbnew.B_Cu)
        t.SetNet(nets[net])
        board.Add(t)
    for net, x, y in r.vias:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt(x, y))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetWidth(FromMM(router.VIA_DIAMETER))
        v.SetDrill(FromMM(router.VIA_DRILL))
        v.SetNet(nets[net])
        board.Add(v)


# ---- checking ----------------------------------------------------------------

def check(name, board_path):
    board = pcbnew.LoadBoard(str(board_path))
    # 1. every pad is on the net the netlist says, and nothing else is connected
    want = netlist_pads(name)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            key = (fp.GetReference(), pad.GetNumber())
            got = pad.GetNetname()
            assert got == want.get(key, ""), f"{name}: {key} is on '{got}', netlist says '{want.get(key, '')}'"
    # 2. placement still matches the placement file
    for row in read_csv(OUT / f"{name}-placement.csv"):
        fp = board.FindFootprintByReference(row["ref"])
        x, y = ToMM(fp.GetPosition().x), ToMM(fp.GetPosition().y)
        if fp.GetFPID().GetLibItemName().wx_str().startswith("RotaryEncoder"):
            x, y = x + EC11_SHAFT[0], y + EC11_SHAFT[1]
        assert abs(x - float(row["x_mm"])) < 0.011 and abs(y - float(row["y_mm"])) < 0.011, row["ref"]
        turn = (fp.GetOrientationDegrees() - float(row["rotation_deg"])) % 360
        assert min(turn, 360 - turn) < 0.01, row["ref"]
    # 3. no part stands where one of the cover's posts presses on the key frame
    if name == "key-frame":
        for px, py in g.all_hold_down_posts():
            px, py = px - g.FRAME_X0, py - g.FRAME_Y0
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    b = pad.GetBoundingBox()
                    dx = max(ToMM(b.GetLeft()) - px, 0, px - ToMM(b.GetRight()))
                    dy = max(ToMM(b.GetTop()) - py, 0, py - ToMM(b.GetBottom()))
                    assert math.hypot(dx, dy) > g.HOLD_DOWN_POST_D / 2 + 0.3, f"{fp.GetReference()} is under a post"
    # 4. KiCad's own design-rule check, including connectivity
    FAB.mkdir(exist_ok=True)
    report = FAB / f"{name}-drc.rpt"
    pcbnew.WriteDRCReport(board, str(report), pcbnew.EDA_UNITS_MILLIMETRES, True)
    body = report.read_text()
    body = re.sub(r"\*\* Created on .*? \*\*\n", "", body)      # keep the committed report stable
    body = re.sub(r"\*\* Drc report for .*? \*\*\n", f"** Drc report for {name}.kicad_pcb **\n", body)
    report.write_text(body)
    # Not about the board: KiCad compares each footprint with the library on the
    # machine running the check, which a CI container may not have configured.
    ignored = len(re.findall(r"^\[lib_footprint_(?:issues|mismatch)\]", body, re.M))
    counts = dict(
        violations=int(re.search(r"Found (\d+) DRC violations", body).group(1)) - ignored,
        unconnected=int(re.search(r"Found (\d+) unconnected pads", body).group(1)),
        footprint=int(re.search(r"Found (\d+) Footprint errors", body).group(1)),
    )
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    length = sum(ToMM(t.GetLength()) for t in tracks)
    print(f"  {name}: {len(board.GetFootprints())} parts, {board.GetNetCount() - 1} nets, "
          f"{len(tracks)} track segments ({length:.0f} mm), {len(vias)} vias; "
          f"DRC violations {counts['violations']}, unconnected {counts['unconnected']}, "
          f"footprint errors {counts['footprint']}")
    return counts


# ---- fabrication outputs -----------------------------------------------------

def export(name, board_path, work):
    gerbers = work / f"{name}-gerbers"
    shutil.rmtree(gerbers, ignore_errors=True)
    gerbers.mkdir(parents=True)
    layers = "F.Cu,B.Cu,F.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
    run = lambda *a: subprocess.run(["kicad-cli", *a], check=True, stdout=subprocess.DEVNULL)  # noqa: E731
    run("pcb", "export", "gerbers", "-o", str(gerbers) + "/", "-l", layers, "--subtract-soldermask", str(board_path))
    run("pcb", "export", "drill", "-o", str(gerbers) + "/", "--format", "excellon",
        "--excellon-units", "mm", "--excellon-separate-th", "--generate-map", "--map-format", "gerberx2",
        str(board_path))
    with zipfile.ZipFile(FAB / f"{name}-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gerbers.iterdir()):
            info = zipfile.ZipInfo(f.name, date_time=(2026, 1, 1, 0, 0, 0))   # stable archive
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, re.sub(rb"(CreationDate,|; DRILL file .*? date )[^*\n]*", rb"\g<1>2026-01-01", f.read_bytes()))
    run("pcb", "export", "pos", "-o", str(FAB / f"{name}-positions.csv"), "--format", "csv", "--units", "mm",
        "--side", "front", str(board_path))
    svg = work / f"{name}.svg"
    run("pcb", "export", "svg", "-o", str(svg), "-l", "B.Cu,F.Cu,F.Fab,F.SilkS,Edge.Cuts",
        "--exclude-drawing-sheet", "--page-size-mode", "2", str(board_path))
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", "2400", "-b", "white", str(svg), "-o", str(FAB / f"{name}.png")], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only check the committed boards")
    ap.add_argument("--board", choices=list(BOARDS), action="append", help="limit to one board")
    ap.add_argument("--work", default="/tmp/tessera-pcb", help="scratch directory")
    args = ap.parse_args()
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in args.board or list(BOARDS):
        path = HERE / name / f"{name}.kicad_pcb"
        if not args.check:
            path.parent.mkdir(exist_ok=True)
            board = new_board(name)
            route(board, name)
            pcbnew.SaveBoard(str(path), board)
        counts = check(name, path)
        ok &= not any(counts.values())
        if not args.check:
            export(name, path, work)
    if not ok:
        sys.exit("FAILED: see hardware/pcb/fab/*-drc.rpt")
    print("boards match the netlist and placement, and pass KiCad DRC")


if __name__ == "__main__":
    main()
