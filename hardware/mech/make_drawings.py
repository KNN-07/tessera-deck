#!/usr/bin/env python3
"""Generates the PCB mechanical drawings and placement files from geometry.py.

    pip install ezdxf matplotlib
    python3 hardware/mech/make_drawings.py

Outputs (hardware/out/):
    key-frame-pcb.dxf, encoder-pcb.dxf   board outline, cut-outs, part positions - import as the board
                                         outline / a mechanical layer in EasyEDA Pro or KiCad
    key-frame-placement.csv, encoder-placement.csv   reference designator positions
    front-layout.png                     dimensioned front view

DXF and CSV coordinates are board-local: origin at the board's top-left corner
seen from the component side, x right, y DOWN (flip y if your tool has y up).
"""
import csv
import pathlib

import ezdxf
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

import geometry as g

OUT = pathlib.Path(__file__).resolve().parent.parent / "out"


def rounded_rect(msp, x0, y0, x1, y1, r, layer):
    """Closed polyline with corner arcs (bulge) - y is flipped for DXF's y-up convention."""
    b = 0.41421356  # tan(22.5 deg): a quarter-circle bulge
    pts = [
        (x0 + r, y0, 0), (x1 - r, y0, b), (x1, y0 + r, 0), (x1, y1 - r, b),
        (x1 - r, y1, 0), (x0 + r, y1, b), (x0, y1 - r, 0), (x0, y0 + r, b),
    ]
    msp.add_lwpolyline([(x, -y, bulge) for x, y, bulge in pts], format="xyb", close=True, dxfattribs={"layer": layer})


def rect(msp, cx, cy, w, h, layer):
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    msp.add_lwpolyline([(x0, -y0), (x1, -y0), (x1, -y1), (x0, -y1)], close=True, dxfattribs={"layer": layer})


def new_doc():
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    for name, color in (("OUTLINE", 7), ("CUTOUT", 1), ("PARTS", 3), ("KEEPOUT", 6), ("NOTES", 8)):
        doc.layers.add(name, color=color)
    return doc, doc.modelspace()


def key_frame():
    """The board that lies on the display glass and carries two tact switches per key."""
    ox, oy = g.FRAME_X0, g.FRAME_Y0  # board origin in lens coordinates
    w, h = g.FRAME_X1 - g.FRAME_X0, g.FRAME_Y1 - g.FRAME_Y0
    doc, msp = new_doc()
    rounded_rect(msp, 0, 0, w, h, 1.0, "OUTLINE")
    parts = []

    for key in range(g.ROWS * g.COLS):
        cx, cy = g.key_center(key)
        cx, cy = cx - ox, cy - oy
        half = g.CUTOUT / 2
        rounded_rect(msp, cx - half, cy - half, cx + half, cy + half, g.CUTOUT_RADIUS, "CUTOUT")
        for side, sign in (("L", -1), ("R", 1)):
            sx = cx + sign * g.SWITCH_OFFSET
            rect(msp, sx, cy, *g.SWITCH_BODY, "PARTS")
            parts.append((f"SW{key}{side}", "C&K PTS810 SJM 250 SMTR LFS", sx, cy, 90, f"key {key}, {'left' if sign < 0 else 'right'}"))

    for i, (x, y) in enumerate(g.hold_down_posts()):
        msp.add_circle((x - ox, -(y - oy)), g.HOLD_DOWN_POST_D / 2 + 0.3, dxfattribs={"layer": "KEEPOUT"})

    # electronics in the strip of board above the top row
    top_free = g.key_center(0)[1] - g.CUTOUT / 2 - oy  # y of the first cut-out edge, board-local
    mid = top_free / 2
    cx = w / 2
    for ref, part, x, y, rot, note, size in (
        ("U1", "TCA9539PWR (TSSOP-24)", cx, mid, 0, "I2C address 0x74: A0 = A1 = GND", (7.8, 6.4)),
        ("C1", "100 nF 0603", cx - 7.5, mid + 2.2, 90, "U1 decoupling", (0.8, 1.6)),
        ("RN1", "4 x 10 k array 0603x4", cx - 18, mid, 0, "pull-ups keys 0-3", (3.2, 1.6)),
        ("RN2", "4 x 10 k array 0603x4", cx - 12, mid, 0, "pull-ups keys 4-7", (3.2, 1.6)),
        ("RN3", "4 x 10 k array 0603x4", cx + 12, mid, 0, "pull-ups keys 8-11", (3.2, 1.6)),
        ("RN4", "4 x 10 k array 0603x4", cx + 18, mid, 0, "pull-ups keys 12-14, RESET", (3.2, 1.6)),
        ("J1", "JST SM04B-SRSS-TB (Qwiic)", w - 26, 3.2, 180, "I2C in, opening to the top edge", (6.0, 4.3)),
        ("J2", "JST SM04B-SRSS-TB (Qwiic)", w - 16, 3.2, 180, "I2C out to the encoder board", (6.0, 4.3)),
    ):
        rect(msp, x, y, *size, "PARTS")
        parts.append((ref, part, x, y, rot, note))

    msp.add_text("TESSERA KEY FRAME rev A - board 1.0 mm, components this side only",
                 height=1.5, dxfattribs={"layer": "NOTES"}).set_placement((2, -(h + 3)))
    doc.saveas(OUT / "key-frame-pcb.dxf")
    write_csv(OUT / "key-frame-placement.csv", parts)
    return w, h, parts


def encoder_board():
    ox, oy = g.ENC_BOARD_X0, g.ENC_BOARD_Y0
    w, h = g.ENC_BOARD_W, g.ENC_BOARD_H
    doc, msp = new_doc()
    rounded_rect(msp, 0, 0, w, h, 1.5, "OUTLINE")
    parts = []
    for i, x in enumerate(g.ENC_X):
        ex, ey = x - ox, g.ENC_Y - oy
        rect(msp, ex, ey, 12.4, 13.2, "PARTS")
        msp.add_circle((ex, -ey), g.ENC_BUSHING_D / 2, dxfattribs={"layer": "PARTS"})
        parts.append((f"ENC{i}", "Alps EC11E series, 20 mm shaft, with push switch", ex, ey, 0, f"dial {i}"))
        parts.append((f"RN{5 + i}", "4 x 10 k array 0603x4", ex + 9.5, ey - 6, 90, f"pull-ups A, B, push of dial {i}"))
        parts.append((f"C{2 + 2 * i}", "10 nF 0603", ex + 9.5, ey + 2, 90, f"dial {i} A filter"))
        parts.append((f"C{3 + 2 * i}", "10 nF 0603", ex + 9.5, ey + 6, 90, f"dial {i} B filter"))
    mid = (g.ENC_X[1] + g.ENC_X[2]) / 2 - ox
    for ref, part, x, y, rot, note, size in (
        ("U2", "TCA9539PWR (TSSOP-24)", mid, h / 2 + 2, 90, "I2C address 0x75: A0 = VCC, A1 = GND", (6.4, 7.8)),
        ("C10", "100 nF 0603", mid, h / 2 - 5, 0, "U2 decoupling", (1.6, 0.8)),
        ("J3", "JST SM04B-SRSS-TB (Qwiic)", 5.0, h / 2, 90, "I2C in", (4.3, 6.0)),
        ("J4", "JST SM04B-SRSS-TB (Qwiic)", w - 5.0, h / 2, 270, "I2C through", (4.3, 6.0)),
    ):
        rect(msp, x, y, *size, "PARTS")
        parts.append((ref, part, x, y, rot, note))
    msp.add_text("TESSERA ENCODER BOARD rev A - board 1.6 mm, encoders on this side",
                 height=1.5, dxfattribs={"layer": "NOTES"}).set_placement((2, -(h + 3)))
    doc.saveas(OUT / "encoder-pcb.dxf")
    write_csv(OUT / "encoder-placement.csv", parts)
    return parts


def write_csv(path, parts):
    with open(path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["ref", "part", "x_mm", "y_mm", "rotation_deg", "note"])
        for ref, part, x, y, rot, note in parts:
            out.writerow([ref, part, f"{x:.2f}", f"{y:.2f}", rot, note])


def preview(frame_parts, enc_parts):
    fig, ax = plt.subplots(figsize=(11, 10.3), dpi=110)
    ax.set_aspect("equal")
    ax.set_xlim(g.CASE_X0 - 4, g.CASE_X1 + 4)
    ax.set_ylim(g.CASE_Y1 + 9, g.CASE_Y0 - 4)
    ax.axis("off")
    case = FancyBboxPatch((g.CASE_X0, g.CASE_Y0), g.CASE_X1 - g.CASE_X0, g.CASE_Y1 - g.CASE_Y0,
                          boxstyle="round,pad=0,rounding_size=3", fc="#f3f4f6", ec="#111827", lw=1.4)
    ax.add_patch(case)
    ax.add_patch(Rectangle((0, 0), g.LENS_W, g.LENS_H, fc="#d1d5db", ec="#374151", lw=1))
    ax.add_patch(Rectangle((g.ACTIVE_X0, g.ACTIVE_Y0), 108.0, 64.8, fc="#111827", ec="none"))
    ax.add_patch(Rectangle((g.FRAME_X0, g.FRAME_Y0), g.FRAME_X1 - g.FRAME_X0, g.FRAME_Y1 - g.FRAME_Y0,
                           fc="#166534", ec="#052e16", lw=1, alpha=0.85))
    for key in range(g.ROWS * g.COLS):
        cx, cy = g.key_center(key)
        ax.add_patch(Rectangle((cx - g.CUTOUT / 2, cy - g.CUTOUT / 2), g.CUTOUT, g.CUTOUT, fc="#111827", ec="#052e16", lw=0.8))
        ax.add_patch(Rectangle((cx - g.KEY_SIZE / 2, cy - g.KEY_SIZE / 2), g.KEY_SIZE, g.KEY_SIZE, fc="#0e7490", ec="none"))
        ax.text(cx, cy, str(key), ha="center", va="center", color="white", fontsize=11)
        for sign in (-1, 1):
            ax.add_patch(Rectangle((cx + sign * g.SWITCH_OFFSET - g.SWITCH_BODY[0] / 2, cy - g.SWITCH_BODY[1] / 2),
                                   *g.SWITCH_BODY, fc="#fbbf24", ec="#78350f", lw=0.6))
    for x, y in g.hold_down_posts():
        ax.add_patch(Circle((x, y), g.HOLD_DOWN_POST_D / 2, fc="none", ec="white", lw=0.8, ls="--"))
    for ref, _part, x, y, _rot, _note in frame_parts:
        if not ref.startswith("SW"):
            ax.text(x + g.FRAME_X0, y + g.FRAME_Y0, ref, ha="center", va="center", color="white", fontsize=6)
    x0, y0, x1, y1 = g.STRIP_WINDOW
    ax.add_patch(Rectangle((g.STRIP_X0, g.STRIP_Y0), g.STRIP_X1 - g.STRIP_X0, g.STRIP_Y1 - g.STRIP_Y0, fc="#7c3aed", ec="none"))
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fc="none", ec="#111827", lw=1.2))
    seg = (g.STRIP_X1 - g.STRIP_X0) / 4
    for i in range(4):
        ax.text(g.STRIP_X0 + seg * (i + 0.5), (g.STRIP_Y0 + g.STRIP_Y1) / 2, f"segment {i}", ha="center", va="center", color="white", fontsize=9)
        if i:
            ax.plot([g.STRIP_X0 + seg * i] * 2, [g.STRIP_Y0, g.STRIP_Y1], color="white", lw=0.5)
    ax.add_patch(Rectangle((g.ENC_BOARD_X0, g.ENC_BOARD_Y0), g.ENC_BOARD_W, g.ENC_BOARD_H, fc="#166534", ec="#052e16", lw=1, alpha=0.85))
    for i, x in enumerate(g.ENC_X):
        ax.add_patch(Circle((x, g.ENC_Y), 7.5, fc="#374151", ec="#111827", lw=1))
        ax.text(x, g.ENC_Y, f"dial {i}", ha="center", va="center", color="white", fontsize=8)
    for x, y in g.COVER_SCREWS:
        ax.add_patch(Circle((x, y), 1.6, fc="white", ec="#111827", lw=0.8))
    ax.add_patch(Rectangle((g.CASE_X1 - 1.0, g.USB_C_Y - 4.5), 2.2, 9, fc="#dc2626", ec="none"))
    ax.text(g.CASE_X1 + 1.6, g.USB_C_Y, "USB-C", rotation=90, ha="left", va="center", fontsize=8, color="#dc2626")

    def dim(xa, xb, y, label):
        ax.annotate("", (xa, y), (xb, y), arrowprops=dict(arrowstyle="<->", lw=0.8, color="#111827"))
        ax.text((xa + xb) / 2, y - 0.8, label, ha="center", va="bottom", fontsize=8)

    dim(g.CASE_X0, g.CASE_X1, g.CASE_Y1 + 5.5, f"{g.CASE_X1 - g.CASE_X0:.1f} mm")
    c0, c1 = g.key_center(10), g.key_center(11)
    dim(c0[0], c1[0], c0[1] + 8.4, f"{g.KEY_PITCH_X:.1f}")
    ax.annotate("", (g.CASE_X0 - 2, g.CASE_Y0), (g.CASE_X0 - 2, g.CASE_Y1), arrowprops=dict(arrowstyle="<->", lw=0.8, color="#111827"))
    ax.text(g.CASE_X0 - 2.8, (g.CASE_Y0 + g.CASE_Y1) / 2, f"{g.CASE_Y1 - g.CASE_Y0:.1f} mm", rotation=90, ha="right", va="center", fontsize=8)
    ax.set_title("Tessera Deck rev A - front view (cover removed). Green: Tessera PCBs, amber: tact switches, "
                 "dashed: cover hold-down posts", fontsize=9)
    fig.savefig(OUT / "front-layout.png", bbox_inches="tight")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, value in g.check().items():
        print(f"{name:42s} {value:7.2f} mm")
    w, h, frame_parts = key_frame()
    enc_parts = encoder_board()
    preview(frame_parts, enc_parts)
    print(f"key-frame PCB {w:.2f} x {h:.2f} mm, encoder PCB {g.ENC_BOARD_W:.1f} x {g.ENC_BOARD_H:.1f} mm -> {OUT}")
