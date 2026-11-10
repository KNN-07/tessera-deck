"""Tessera Deck rev A - single source of truth for every mechanical dimension.

Coordinates: millimetres, origin at the top-left corner of the display's cover
lens seen from the front, x to the right, y down, z up out of the screen with
z = 0 on the lens surface.

Display numbers come from Waveshare's drawing and 3D model of the
ESP32-S3-Touch-LCD-5 (800 x 480). The one assumption is marked ASSUMED.
"""

# ---- display module (Waveshare ESP32-S3-Touch-LCD-5) -------------------------
LENS_W, LENS_H = 127.70, 87.45
VIEW_X0, VIEW_Y0, VIEW_W, VIEW_H = 9.35, 9.35, 109.00, 65.80
PIXEL_PITCH = 0.135            # 108.0 x 64.8 mm active area / 800 x 480
# ASSUMED: the active area is centred in the viewing window. Check on your
# panel with the simulator's splash before ordering boards.
ACTIVE_X0 = VIEW_X0 + (VIEW_W - 800 * PIXEL_PITCH) / 2
ACTIVE_Y0 = VIEW_Y0 + (VIEW_H - 480 * PIXEL_PITCH) / 2

BOARD_X0, BOARD_Y0, BOARD_W, BOARD_H = 3.59, 6.24, 120.52, 75.52   # the module's own PCB
BOARD_FRONT_Z, BOARD_BACK_Z = -4.8, -6.4
BOARD_STANDOFFS = [(8.35, 12.75), (119.35, 12.75), (8.35, 75.75), (119.35, 75.75)]  # M2.5, on the back
BOARD_STANDOFF_END_Z = -10.4
BOARD_DEEPEST_Z = -13.4        # screw-terminal block on the back
USB_C_Y, USB_C_Z = 44.2, -7.6  # centre of the receptacle; it faces +x, mouth at x = 125.1
USB_C_MOUTH_X = 125.1

# ---- screen layout in pixels: must equal td_layout_rev_a in firmware/core/td_device.c
ROWS, COLS = 3, 5
KEY_PX = 96
KEY_X0_PX, KEY_Y0_PX = 32, 8
KEY_PITCH_X_PX, KEY_PITCH_Y_PX = 160, 124
STRIP_Y_PX, STRIP_H_PX = 380, 100
ENCODERS = 4


def px_x(px):
    return ACTIVE_X0 + px * PIXEL_PITCH


def px_y(px):
    return ACTIVE_Y0 + px * PIXEL_PITCH


KEY_SIZE = KEY_PX * PIXEL_PITCH                    # 12.96
KEY_PITCH_X = KEY_PITCH_X_PX * PIXEL_PITCH         # 21.60
KEY_PITCH_Y = KEY_PITCH_Y_PX * PIXEL_PITCH         # 16.74


def key_center(key):
    row, col = divmod(key, COLS)
    return (px_x(KEY_X0_PX + col * KEY_PITCH_X_PX) + KEY_SIZE / 2,
            px_y(KEY_Y0_PX + row * KEY_PITCH_Y_PX) + KEY_SIZE / 2)


STRIP_X0, STRIP_X1 = px_x(0), px_x(800)
STRIP_Y0, STRIP_Y1 = px_y(STRIP_Y_PX), px_y(STRIP_Y_PX + STRIP_H_PX)

# ---- key mechanism -----------------------------------------------------------
PIPE = 13.0                    # light pipe of a keycap, square
CUTOUT = 13.4                  # window in the key-frame PCB
CUTOUT_RADIUS = 0.8            # router bit radius allowance
OPENING = 13.6                 # window in the top cover
SWITCH_OFFSET = 8.8            # tact switch centre, left and right of the key centre
SWITCH_BODY = (3.2, 4.2)       # C&K PTS810, long axis along y
SWITCH_HEIGHT = 2.5
SWITCH_TRAVEL = 0.25
WING_REACH = 3.9               # how far a cap wing extends beyond the light pipe
WING_LEN = 8.0                 # along y
WING_THICK = 1.5

FRAME_PCB_THICK = 1.0
FRAME_X0, FRAME_X1 = 1.0, LENS_W - 1.0
FRAME_Y0 = 1.0
FRAME_Y1 = key_center(10)[1] + CUTOUT / 2 + 1.6    # 1.6 mm of board below the bottom row

Z_PCB_TOP = FRAME_PCB_THICK
Z_SWITCH_TOP = Z_PCB_TOP + SWITCH_HEIGHT           # caps rest here
Z_WING_TOP = Z_SWITCH_TOP + WING_THICK
Z_COVER_UNDERSIDE = Z_WING_TOP + 0.2
COVER_PLATE = 1.6
Z_COVER_TOP = Z_COVER_UNDERSIDE + COVER_PLATE
Z_CAP_TOP = Z_COVER_TOP + 0.6
Z_PIPE_BOTTOM = 0.5            # rest gap between a light pipe and the lens

HOLD_DOWN_POST_D = 3.0         # cover posts that press the frame PCB onto the lens


def hold_down_posts():
    """Where bars between columns cross webs between rows: clear of switches and wings."""
    pts = []
    for row in range(ROWS - 1):
        y = (key_center(row * COLS)[1] + key_center((row + 1) * COLS)[1]) / 2
        for col in range(COLS - 1):
            x = (key_center(col)[0] + key_center(col + 1)[0]) / 2
            pts.append((x, y))
    return pts



def all_hold_down_posts():
    """The posts between the keys plus those along the two sides and in the top corners."""
    posts = hold_down_posts()
    for y in sorted({y for _, y in posts}):
        posts += [(FRAME_X0 + 2.5, y), (FRAME_X1 - 2.5, y)]
    return posts + [(FRAME_X0 + 3, FRAME_Y0 + 3), (FRAME_X1 - 3, FRAME_Y0 + 3)]

# ---- encoder board -----------------------------------------------------------
ENC_Y = LENS_H + 13.5
ENC_X = [px_x(100 + 200 * i) for i in range(ENCODERS)]   # centred under the strip segments
ENC_BOARD_W, ENC_BOARD_H = 112.0, 22.0
ENC_BOARD_X0 = LENS_W / 2 - ENC_BOARD_W / 2
ENC_BOARD_Y0 = ENC_Y - ENC_BOARD_H / 2
ENC_BODY_HEIGHT = 7.0          # EC11 body above its PCB
ENC_BUSHING_D = 7.0            # M7 x 0.75 threaded bushing
DECK_PLATE = 1.5

# ---- case --------------------------------------------------------------------
WALL = 2.4
SIDE_GAP = 0.5                 # lens to side wall
TOP_MARGIN = 6.0               # space above the lens for bosses and the I2C cable
CASE_X0, CASE_X1 = -SIDE_GAP - WALL, LENS_W + SIDE_GAP + WALL
CASE_Y0 = -TOP_MARGIN - WALL
CASE_Y1 = LENS_H + 27.0 + WALL
FLOOR_Z = -16.0                # inside floor; 2.6 mm under the terminal block
FLOOR_THICK = 2.0
KEY_BLOCK_Y1 = FRAME_Y1 + 0.4 + 1.0       # pocket clearance + front wall of the key block
STRIP_WINDOW = (VIEW_X0, STRIP_Y0 - 0.25, VIEW_X0 + VIEW_W, VIEW_Y0 + VIEW_H)   # x0, y0, x1, y1
COVER_SCREWS = [(CASE_X0 + 5.5, CASE_Y0 + 4.2), (CASE_X1 - 5.5, CASE_Y0 + 4.2),   # clear of the lens
                (CASE_X0 + 5.5, CASE_Y1 - 5.0), (CASE_X1 - 5.5, CASE_Y1 - 5.0)]   # M3
USB_CUTOUT = (13.0, 7.5)       # along y, along z: room for a plug's overmould


def firmware_layout():
    """Parses td_layout_rev_a so tests can prove this file and the firmware agree."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[2] / "firmware" / "core" / "td_device.c").read_text()
    block = src[src.index("td_layout_rev_a = {"):]
    block = block[: block.index("};")]
    return {k: int(v) for k, v in re.findall(r"\.(\w+) = (\d+)", block)}


def check():
    fw = firmware_layout()
    expected = {
        "panel_w": 800, "panel_h": 480, "rows": ROWS, "cols": COLS, "key_w": KEY_PX, "key_h": KEY_PX,
        "key_x0": KEY_X0_PX, "key_y0": KEY_Y0_PX, "key_pitch_x": KEY_PITCH_X_PX, "key_pitch_y": KEY_PITCH_Y_PX,
        "encoders": ENCODERS, "strip_y": STRIP_Y_PX, "strip_h": STRIP_H_PX,
    }
    for name, value in expected.items():
        assert fw[name] == value, f"{name}: firmware {fw[name]} != geometry {value}"

    # clearances the design depends on
    bar = KEY_PITCH_X - CUTOUT
    web = KEY_PITCH_Y - CUTOUT
    switch_gap = KEY_PITCH_X - 2 * SWITCH_OFFSET - SWITCH_BODY[0]
    switch_to_cutout = SWITCH_OFFSET - SWITCH_BODY[0] / 2 - CUTOUT / 2
    wing_gap = KEY_PITCH_X - 2 * (PIPE / 2 + WING_REACH)
    post_to_wing = web / 2 + CUTOUT / 2 - HOLD_DOWN_POST_D / 2 - WING_LEN / 2
    assert switch_gap >= 0.75 and switch_to_cutout >= 0.45, (switch_gap, switch_to_cutout)
    assert wing_gap >= 0.75 and post_to_wing >= 0.8, (wing_gap, post_to_wing)
    assert web >= HOLD_DOWN_POST_D + 0.3, web
    assert FRAME_Y1 + 0.4 < STRIP_WINDOW[1] - 0.9, "key block wall would overlap the strip window"
    assert PIPE >= KEY_SIZE and OPENING > PIPE and CUTOUT > PIPE
    assert Z_PIPE_BOTTOM - SWITCH_TRAVEL > 0.1
    return {
        "key image": KEY_SIZE, "key pitch x": KEY_PITCH_X, "key pitch y": KEY_PITCH_Y,
        "pcb bar between columns": bar, "pcb web between rows": web,
        "gap between neighbouring switches": switch_gap, "switch body to cutout edge": switch_to_cutout,
        "gap between neighbouring cap wings": wing_gap, "frame pcb height": FRAME_Y1 - FRAME_Y0,
        "cover top above lens": Z_COVER_TOP, "cap top above lens": Z_CAP_TOP,
        "case outer width": CASE_X1 - CASE_X0, "case outer depth": CASE_Y1 - CASE_Y0,
        "case outer height (without caps, knobs)": Z_COVER_TOP - (FLOOR_Z - FLOOR_THICK),
    }


if __name__ == "__main__":
    for name, value in check().items():
        print(f"{name:42s} {value:7.2f} mm")
