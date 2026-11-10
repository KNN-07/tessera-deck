#!/usr/bin/env python3
"""Tessera Deck rev A enclosure and keycap, generated with CadQuery from ../mech/geometry.py.

    pip install cadquery
    python3 hardware/case/case.py          # writes STEP + STL to hardware/out/ and runs fit checks

Parts (print orientation in hardware/README.md):
    keycap   x15  clear resin (SLA) or machined acrylic - the light pipe must be transparent
    cover    x1   top shell: key openings, strip window, dial holes
    tray     x1   bottom shell: carries the display board on its own M2.5 standoffs

All coordinates follow geometry.py: origin = top-left corner of the display lens,
y towards the user, z up from the lens surface. CadQuery solids here use (x, -y, z)
so that the model is right-handed and looks correct in a viewer.
"""
import pathlib
import sys

import cadquery as cq

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "mech"))
import geometry as g  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"


def box(x0, y0, x1, y1, z0, z1):
    """Axis-aligned box from geometry coordinates (y down) to model coordinates (y up)."""
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, -y1, z0))


def cyl(x, y, d, z0, z1):
    return cq.Workplane("XY").circle(d / 2).extrude(z1 - z0).translate((x, -y, z0))


def keycap():
    """Clear cap: a square light pipe with two wings that rest on the tact switches.
    Modelled at key centre (0, 0); z as assembled, at rest."""
    half = g.PIPE / 2
    pipe = box(-half, -half, half, half, g.Z_PIPE_BOTTOM, g.Z_CAP_TOP)
    pipe = pipe.faces(">Z").edges().chamfer(0.4)
    cap = pipe
    for sign in (-1, 1):
        x0, x1 = sorted((sign * half, sign * (half + g.WING_REACH)))
        cap = cap.union(box(x0, -g.WING_LEN / 2, x1, g.WING_LEN / 2, g.Z_SWITCH_TOP, g.Z_WING_TOP))
    return cap


def cover():
    c = g
    # key block: a 1.6 mm plate on walls, hollow underneath for the frame PCB, switches and cap wings
    part = box(c.CASE_X0, c.CASE_Y0, c.CASE_X1, c.KEY_BLOCK_Y1, 0, c.Z_COVER_TOP)
    part = part.cut(box(c.FRAME_X0 - 0.4, c.FRAME_Y0 - 0.4, c.FRAME_X1 + 0.4, c.FRAME_Y1 + 0.4, 0, c.Z_COVER_UNDERSIDE))
    # strip surround and dial deck: a thin plate, so the strip stays reachable
    part = part.union(box(c.CASE_X0, c.KEY_BLOCK_Y1, c.CASE_X1, c.CASE_Y1, 0, c.DECK_PLATE))
    x0, y0, x1, y1 = c.STRIP_WINDOW
    part = part.cut(box(x0, y0, x1, y1, -1, c.DECK_PLATE + 1))

    for key in range(c.ROWS * c.COLS):
        cx, cy = c.key_center(key)
        h = c.OPENING / 2
        part = part.cut(box(cx - h, cy - h, cx + h, cy + h, c.Z_COVER_UNDERSIDE - 1, c.Z_COVER_TOP + 1))

    # posts that press the frame PCB onto the lens
    posts = list(c.hold_down_posts())
    web_ys = sorted({y for _, y in posts})
    for y in web_ys:
        posts += [(c.FRAME_X0 + 2.5, y), (c.FRAME_X1 - 2.5, y)]
    posts += [(c.FRAME_X0 + 3, c.FRAME_Y0 + 3), (c.FRAME_X1 - 3, c.FRAME_Y0 + 3)]
    for x, y in posts:
        part = part.union(cyl(x, y, c.HOLD_DOWN_POST_D, c.Z_PCB_TOP, c.Z_COVER_UNDERSIDE + 0.1))

    # cable channel from the Qwiic connectors (top-right of the frame PCB) down into the tray
    part = part.cut(box(c.FRAME_X1 - 34, c.CASE_Y0 + c.WALL, c.FRAME_X1 - 8, c.FRAME_Y0, 0, 4.4))

    for x in c.ENC_X:
        part = part.cut(cyl(x, c.ENC_Y, c.ENC_BUSHING_D + 0.3, -1, c.DECK_PLATE + 1))
    for i, (x, y) in enumerate(c.COVER_SCREWS):
        part = part.cut(cyl(x, y, 3.4, -1, c.Z_COVER_TOP + 1))
        if i < 2:  # the two screws in the thick key block get recessed heads
            part = part.cut(cyl(x, y, 6.5, c.Z_COVER_TOP - 3.4, c.Z_COVER_TOP + 1))
    return part


def tray():
    c = g
    bottom = c.FLOOR_Z - c.FLOOR_THICK
    part = box(c.CASE_X0, c.CASE_Y0, c.CASE_X1, c.CASE_Y1, bottom, 0)
    part = part.cut(box(c.CASE_X0 + c.WALL, c.CASE_Y0 + c.WALL, c.CASE_X1 - c.WALL, c.CASE_Y1 - c.WALL, c.FLOOR_Z, 1))

    # the display board sits on posts under its four M2.5 standoffs, screwed from below
    for x, y in c.BOARD_STANDOFFS:
        part = part.union(cyl(x, y, 7.0, c.FLOOR_Z, c.BOARD_STANDOFF_END_Z))
        part = part.cut(cyl(x, y, 2.9, bottom - 1, c.BOARD_STANDOFF_END_Z + 1))
        part = part.cut(cyl(x, y, 5.2, bottom - 1, bottom + 1.0))

    # corner posts for the cover screws: M3 heat-set inserts pressed in from the top
    for x, y in c.COVER_SCREWS:
        part = part.union(cyl(x, y, 8.0, c.FLOOR_Z, 0))
        part = part.cut(cyl(x, y, 4.0, -6.0, 1))

    w, h = c.USB_CUTOUT
    part = part.cut(box(c.CASE_X1 - c.WALL - 1, c.USB_C_Y - w / 2, c.CASE_X1 + 1, c.USB_C_Y + w / 2,
                        c.USB_C_Z - h / 2, c.USB_C_Z + h / 2))
    return part


def display_envelope():
    """Keep-out volumes of the display module, for the fit checks only."""
    c = g
    lens_and_lcd = box(0, 0, c.LENS_W, c.LENS_H, c.BOARD_FRONT_Z, 0)
    board = box(c.BOARD_X0, c.BOARD_Y0, c.BOARD_X0 + c.BOARD_W, c.BOARD_Y0 + c.BOARD_H, c.BOARD_BACK_Z, c.BOARD_FRONT_Z)
    back_parts = box(c.BOARD_X0 + 9, c.BOARD_Y0 + 2, c.BOARD_X0 + c.BOARD_W - 9, c.BOARD_Y0 + c.BOARD_H - 2,
                     c.BOARD_DEEPEST_Z, c.BOARD_BACK_Z)
    return lens_and_lcd.union(board).union(back_parts)


def frame_pcb():
    """Returns (bare board, switch bodies)."""
    c = g
    pcb = box(c.FRAME_X0, c.FRAME_Y0, c.FRAME_X1, c.FRAME_Y1, 0, c.Z_PCB_TOP)
    switches = None
    for key in range(c.ROWS * c.COLS):
        cx, cy = c.key_center(key)
        h = c.CUTOUT / 2
        pcb = pcb.cut(box(cx - h, cy - h, cx + h, cy + h, -1, 2))
        for sign in (-1, 1):
            sx = cx + sign * c.SWITCH_OFFSET
            body = box(sx - c.SWITCH_BODY[0] / 2, cy - c.SWITCH_BODY[1] / 2, sx + c.SWITCH_BODY[0] / 2,
                       cy + c.SWITCH_BODY[1] / 2, c.Z_PCB_TOP, c.Z_SWITCH_TOP)
            switches = body if switches is None else switches.union(body)
    return pcb, switches


def overlap(a, b):
    """Volume shared by two solids, in mm^3."""
    common = a.intersect(b)
    return sum(s.Volume() for s in common.solids().vals())


def check(parts):
    c = g
    cap, cov, tr = parts["keycap"], parts["cover"], parts["tray"]
    for name, part in parts.items():
        solids = part.solids().vals()
        assert len(solids) == 1 and solids[0].isValid(), f"{name}: expected one valid solid, got {len(solids)}"
    caps = None
    for key in range(c.ROWS * c.COLS):
        cx, cy = c.key_center(key)
        placed = cap.translate((cx, -cy, 0))
        caps = placed if caps is None else caps.union(placed)
    (pcb, switches), display = frame_pcb(), display_envelope()
    pressed = caps.translate((0, 0, -c.SWITCH_TRAVEL))
    results = {
        "cover / tray": overlap(cov, tr),
        "cover / caps at rest": overlap(cov, caps),
        "cover / caps pressed": overlap(cov, pressed),
        "cover / frame PCB": overlap(cov, pcb),
        "cover / switches": overlap(cov, switches),
        "caps at rest / switches (wings rest on them)": overlap(caps, switches),
        "caps pressed / frame PCB": overlap(pressed, pcb),
        "caps pressed / display": overlap(pressed, display),
        "tray / display": overlap(tr, display),
        "cover / display": overlap(cov, display),
    }
    for name, volume in results.items():
        print(f"  interference {name:46s} {volume:8.3f} mm^3")
        assert volume < 0.01, f"{name} collide"
    # the cover must trap the caps: the wings may not fit through the openings
    assert c.PIPE + 2 * c.WING_REACH > c.OPENING + 2
    # every wing must sit over a switch plunger (the middle of the switch body)
    assert c.PIPE / 2 < c.SWITCH_OFFSET < c.PIPE / 2 + c.WING_REACH
    pcb = pcb.union(switches)
    return {"caps": caps, "pcb": pcb, "display": display}


def main():
    OUT.mkdir(exist_ok=True)
    g.check()
    parts = {"keycap": keycap(), "cover": cover(), "tray": tray()}
    extras = check(parts)
    for name, part in parts.items():
        cq.exporters.export(part, str(OUT / f"{name}.step"))
        cq.exporters.export(part, str(OUT / f"{name}.stl"), tolerance=0.05, angularTolerance=0.2)
        bb = part.val().BoundingBox()
        print(f"  {name:7s} {bb.xlen:6.1f} x {bb.ylen:6.1f} x {bb.zlen:5.1f} mm, {part.val().Volume() / 1000:6.1f} cm^3")

    assembly = (cq.Assembly()
                .add(parts["tray"], name="tray", color=cq.Color(0.20, 0.22, 0.25))
                .add(extras["display"], name="display", color=cq.Color(0.05, 0.05, 0.06))
                .add(extras["pcb"], name="frame_pcb", color=cq.Color(0.1, 0.4, 0.2))
                .add(parts["cover"], name="cover", color=cq.Color(0.20, 0.22, 0.25))
                .add(extras["caps"], name="caps", color=cq.Color(0.8, 0.9, 1.0, 0.5)))
    assembly.save(str(OUT / "assembly.step"))

    # section through the middle row of keys, for docs
    cy = g.key_center(5)[1]
    section_parts = parts["tray"].union(parts["cover"]).union(extras["caps"]).union(extras["pcb"]).union(extras["display"])
    section = section_parts.intersect(box(g.CASE_X0 - 1, cy - 0.05, g.CASE_X1 + 1, cy + 0.05, -30, 30))
    cq.exporters.export(section, str(OUT / "section-middle-row.svg"),
                        opt={"projectionDir": (0, -1, 0), "showAxes": False, "showHidden": False, "width": 1400, "height": 420})
    cq.exporters.export(parts["cover"].union(extras["caps"]), str(OUT / "cover-iso.svg"),
                        opt={"projectionDir": (0.6, -1.0, 0.9), "showAxes": False, "showHidden": False, "width": 1200, "height": 800})
    print("wrote", OUT)


if __name__ == "__main__":
    main()
