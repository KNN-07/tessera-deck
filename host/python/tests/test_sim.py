"""End-to-end tests: the Python host library against the firmware core in the simulator."""
import io

import pytest
from PIL import Image

from tessera import (DeckConfig, DeckError, EncoderButtonEvent, EncoderRotateEvent, KeyEvent, PowerEvent, TouchEvent)
from tessera import protocol as P


def close_to(pixel, color, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(pixel, color))


def drain(deck):
    events = []
    while True:
        ev = deck.read_event(timeout=0.3)
        if ev is None:
            return events
        events.append(ev)


def test_info(deck):
    info = deck.info
    assert (info.rows, info.cols, info.key_size) == (3, 5, (96, 96))
    assert (info.encoders, info.strip_size, info.strip_segments) == (4, (800, 100), 4)
    assert info.model == "tessera-a5" and info.serial.startswith("SIM")
    assert info.max_payload >= 800 * 100 * 2 + 10
    assert deck.ping(b"hello") == b"hello"


def test_key_images_land_on_the_right_keys(sim, deck, tmp_path):
    deck.set_brightness(100)
    deck.fill_all((0, 0, 0))
    colors = {0: (255, 0, 0), 4: (0, 255, 0), 7: (0, 0, 255), 14: (255, 255, 0)}
    for key, color in colors.items():
        deck.set_key_image(key, Image.new("RGB", (144, 144), color), fmt="jpeg" if key % 2 else "rgb565")
    shot = sim.screenshot(tmp_path)
    for key, color in colors.items():
        row, col = divmod(key, 5)
        x, y = 32 + 160 * col, 8 + 124 * row
        for dx, dy in ((2, 2), (48, 48), (93, 93)):
            assert close_to(shot.getpixel((x + dx, y + dy)), color), (key, dx, dy)
        assert close_to(shot.getpixel((x - 2, y + 48)), (0, 0, 0))
        assert close_to(shot.getpixel((x + 98, y + 48)), (0, 0, 0))
    assert close_to(shot.getpixel((32 + 160 + 48, 8 + 48)), (0, 0, 0))  # key 1 untouched


def test_photo_like_jpeg_survives(sim, deck, tmp_path):
    """A detailed image decodes close to what Pillow decodes from the same JPEG."""
    deck.set_brightness(100)
    src = Image.effect_mandelbrot((96, 96), (-2, -1.5, 1, 1.5), 60).convert("RGB")
    from tessera import images
    data = images.to_jpeg(src, (96, 96))
    deck.set_key_data(3, P.FMT_JPEG, data)
    ref = Image.open(io.BytesIO(data)).convert("RGB")
    shot = sim.screenshot(tmp_path).crop((32 + 3 * 160, 8, 32 + 3 * 160 + 96, 8 + 96))
    diff = sum(abs(a - b) for a, b in zip(ref.tobytes(), shot.tobytes())) / (96 * 96 * 3)
    assert diff < 6, diff


def test_strip_segments_and_full_strip(sim, deck, tmp_path):
    deck.set_brightness(100)
    deck.set_strip_image(Image.new("RGB", (800, 100), (10, 200, 10)), fmt="rgb565")
    deck.set_segment_image(2, Image.new("RGB", (50, 50), (200, 10, 200)))
    shot = sim.screenshot(tmp_path)
    assert close_to(shot.getpixel((399, 380 + 50)), (10, 200, 10))
    assert close_to(shot.getpixel((401, 380 + 1)), (200, 10, 200))
    assert close_to(shot.getpixel((599, 380 + 98)), (200, 10, 200))
    assert close_to(shot.getpixel((601, 380 + 50)), (10, 200, 10))
    assert not close_to(shot.getpixel((400, 379)), (200, 10, 200))


def test_rejections(deck):
    with pytest.raises(DeckError) as e:
        deck.set_key_data(0, P.FMT_JPEG, b"not a jpeg")
    assert e.value.status == 4
    with pytest.raises(DeckError) as e:
        deck.set_key_data(99, P.FMT_RGB565, b"\0" * (96 * 96 * 2))
    assert e.value.status == 3
    with pytest.raises(DeckError):
        deck.set_brightness(200)
    with pytest.raises(DeckError):
        deck.set_strip_data(700, 0, 200, 100, P.FMT_RGB565, b"\0" * 40000)
    assert deck.ping(b"still alive") == b"still alive"


def test_brightness_dims_the_picture(sim, deck, tmp_path):
    deck.fill_all((255, 255, 255))
    deck.set_brightness(100)
    assert sim.ctl.backlight() == (True, 100)
    full = sim.screenshot(tmp_path).getpixel((10, 10))
    deck.set_brightness(20)
    dim = sim.screenshot(tmp_path).getpixel((10, 10))
    assert full[0] > 240 and 90 < dim[0] < 150
    deck.set_power(False)
    assert sim.ctl.backlight()[0] is False
    deck.set_power(True)
    assert sim.ctl.backlight() == (True, 20)


def test_input_events(sim, deck):
    sim.press(0)
    sim.press(14)
    sim.ctl.turn(2, 3)
    sim.ctl.turn(2, -1)
    sim.ctl.encoder_button(1, True)
    sim.ctl.advance(20)
    sim.ctl.encoder_button(1, False)
    sim.ctl.advance(20)
    assert drain(deck) == [
        KeyEvent(0, True), KeyEvent(0, False), KeyEvent(14, True), KeyEvent(14, False),
        EncoderRotateEvent(2, 1), EncoderRotateEvent(2, 1), EncoderRotateEvent(2, 1), EncoderRotateEvent(2, -1),
        EncoderButtonEvent(1, True), EncoderButtonEvent(1, False),
    ]
    state = deck.get_state()
    assert state.keys == 0 and state.rx_errors == 0


def test_touch_gestures(sim, deck):
    sim.ctl.touch("down", 650, 400)
    sim.ctl.advance(50)
    sim.ctl.touch("up")
    sim.ctl.touch("down", 100, 420)
    sim.ctl.advance(600)
    sim.ctl.touch("up")
    sim.ctl.touch("down", 100, 420)
    sim.ctl.advance(20)
    sim.ctl.touch("move", 500, 430)
    sim.ctl.touch("up")
    sim.ctl.touch("down", 100, 100)  # on the keys, not the strip
    sim.ctl.touch("up")
    assert drain(deck) == [
        TouchEvent("tap", 650, 20, 650, 20),
        TouchEvent("long", 100, 40, 100, 40),
        TouchEvent("drag", 100, 40, 500, 50),
    ]


def test_sleep_and_wake(sim, deck):
    cfg = deck.get_config()
    assert cfg == DeckConfig()
    cfg.sleep_timeout_s = 30
    deck.set_config(cfg)
    sim.ctl.advance(31_000)
    assert sim.ctl.backlight()[0] is False
    sim.press(5)  # wakes, not reported
    sim.press(5)  # reported
    assert drain(deck) == [PowerEvent(False), PowerEvent(True), KeyEvent(5, True), KeyEvent(5, False)]
    sim.ctl.suspend(True)
    assert sim.ctl.backlight()[0] is False
    sim.ctl.suspend(False)
    assert sim.ctl.backlight()[0] is True


def test_config_and_splash_survive_reboot(sim, deck, tmp_path):
    deck.set_config(DeckConfig(default_brightness=100, enc_invert_mask=0b0101, long_press_ms=800))
    deck.fill_all((0, 0, 0))
    deck.set_key_image(6, Image.new("RGB", (96, 96), (255, 128, 0)))
    deck.save_splash()
    deck.fill_all((255, 255, 255))
    deck.reboot()
    assert sim.ctl.reboots() == (1, 0)
    assert deck.get_config().enc_invert_mask == 0b0101
    shot = sim.screenshot(tmp_path)
    assert close_to(shot.getpixel((32 + 160 + 48, 8 + 124 + 48)), (255, 128, 0))
    assert close_to(shot.getpixel((5, 5)), (0, 0, 0))
    sim.ctl.turn(0, 1)
    assert drain(deck) == [EncoderRotateEvent(0, -1)]
    deck.clear_splash()
    deck.reset()
    assert not close_to(sim.screenshot(tmp_path).getpixel((32 + 160 + 48, 8 + 124 + 48)), (255, 128, 0))
    deck.reboot(bootloader=True)
    assert sim.ctl.reboots() == (2, 1)


def test_host_disconnect_restores_splash(sim, tmp_path):
    d = sim.deck()
    d.set_brightness(100)
    d.fill_all((255, 0, 255))
    assert close_to(sim.screenshot(tmp_path).getpixel((5, 5)), (255, 0, 255))
    d.close()
    sim.ctl.advance(1)  # let the simulator notice the closed socket
    for _ in range(50):
        if not close_to(sim.screenshot(tmp_path).getpixel((5, 5)), (255, 0, 255)):
            break
        sim.ctl.advance(1)
    assert not close_to(sim.screenshot(tmp_path).getpixel((5, 5)), (255, 0, 255))
    d2 = sim.deck()  # and a new host can attach
    assert d2.ping(b"x") == b"x"
    d2.close()


def test_recovers_from_line_noise(sim):
    d = sim.deck(timeout=0.5, retries=2)
    d._t.write(b"\x00\xffTDgarbage that is not a frame")
    d._t.write(P.encode_frame(P.CMD_PING, 1, b"x")[:-2])  # truncated frame
    sim.ctl.advance(1500)  # the partial frame times out on the deck
    assert d.ping(b"after noise") == b"after noise"
    assert d.get_state().rx_errors >= 1
    d.close()
