"""Integration test: mock OpenDeck <-websocket-> plugin <-TDP-> firmware simulator.

Plays the part of OpenDeck's plugin host (the OpenAction websocket protocol) and
checks both directions. Needs `pip install pytest websockets pillow` plus:

    cmake -S firmware -B firmware/build && cmake --build firmware/build
    cargo build --release --manifest-path host/opendeck-plugin/Cargo.toml
"""
import asyncio
import base64
import io
import json
import os
import pathlib
import socket
import subprocess
import sys

import pytest

websockets = pytest.importorskip("websockets")
from PIL import Image  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "host" / "python"))
from tessera.simctl import SimControl  # noqa: E402

SIM = REPO / "firmware" / "build" / "tessera-sim"
PLUGIN = REPO / "host" / "opendeck-plugin" / "target" / "release" / "opendeck-tessera"
UUID = "org.tessera-deck.opendeck"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def data_url(color, size, fmt):
    buf = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", size, color).save(buf, fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def close_to(pixel, color, tol=14):
    return all(abs(a - b) <= tol for a, b in zip(pixel, color))


async def scenario(tmp_path):
    sim_port, ctl_port, ws_port = free_port(), free_port(), free_port()
    sim = subprocess.Popen([str(SIM), "--port", str(sim_port), "--ctl", str(ctl_port), "--state", str(tmp_path / "state")],
                           stdout=subprocess.PIPE)
    sim.stdout.readline()
    ctl = SimControl(port=ctl_port)
    inbox: asyncio.Queue = asyncio.Queue()
    connection = {}

    async def handler(ws):
        connection["ws"] = ws
        async for message in ws:
            await inbox.put(json.loads(message))

    async def expect(event, timeout=10):
        while True:
            msg = await asyncio.wait_for(inbox.get(), timeout)
            if msg.get("event") == event:
                return msg

    def shot():
        path = tmp_path / "shot.ppm"
        ctl.screenshot(str(path))
        return Image.open(path).convert("RGB")

    async def settle():
        await asyncio.sleep(0.4)

    plugin = None
    try:
        async with websockets.serve(handler, "127.0.0.1", ws_port):
            env = dict(os.environ, TESSERA_DEVICE=f"tcp://127.0.0.1:{sim_port}")
            plugin = subprocess.Popen(
                [str(PLUGIN), "-port", str(ws_port), "-pluginUUID", UUID, "-registerEvent", "registerPlugin",
                 "-info", json.dumps({"devices": []})], env=env)

            assert (await expect("registerPlugin"))["uuid"] == UUID
            reg = (await expect("registerDevice"))["payload"]
            assert reg["id"].startswith("td-SIM")
            assert (reg["rows"], reg["columns"], reg["encoders"], reg["type"]) == (3, 5, 4, 7)
            dev, ws = reg["id"], connection["ws"]

            # OpenDeck -> deck
            await ws.send(json.dumps({"event": "setBrightness", "device": dev, "brightness": 100}))
            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": "Keypad", "position": 7,
                                      "image": data_url((255, 0, 0, 255), (144, 144), "PNG")}))
            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": "Keypad", "position": 8,
                                      "image": data_url((0, 255, 0, 128), (72, 72), "PNG")}))
            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": "Encoder", "position": 1,
                                      "image": data_url((0, 0, 255), (200, 100), "JPEG")}))
            await settle()
            im = shot()
            assert ctl.backlight() == (True, 100)
            assert close_to(im.getpixel((32 + 2 * 160 + 48, 8 + 124 + 48)), (255, 0, 0))
            assert close_to(im.getpixel((32 + 3 * 160 + 48, 8 + 124 + 48)), (0, 128, 0))  # 50% alpha over black
            assert close_to(im.getpixel((300, 380 + 50)), (0, 0, 255))
            assert not close_to(im.getpixel((100, 380 + 50)), (0, 0, 255))

            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": "Keypad", "position": 7, "image": None}))
            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": "Encoder", "position": 1, "image": None}))
            await settle()
            im = shot()
            assert close_to(im.getpixel((32 + 2 * 160 + 48, 8 + 124 + 48)), (0, 0, 0))
            assert close_to(im.getpixel((300, 380 + 50)), (0, 0, 0))
            assert close_to(im.getpixel((32 + 3 * 160 + 48, 8 + 124 + 48)), (0, 128, 0))
            await ws.send(json.dumps({"event": "setImage", "device": dev, "controller": None, "position": None, "image": None}))
            await settle()
            assert close_to(shot().getpixel((32 + 3 * 160 + 48, 8 + 124 + 48)), (0, 0, 0))

            # deck -> OpenDeck
            ctl.key(3, True)
            assert (await expect("keyDown"))["payload"] == {"device": dev, "position": 3}
            ctl.key(3, False)
            assert (await expect("keyUp"))["payload"] == {"device": dev, "position": 3}
            ctl.turn(2, -1)
            assert (await expect("encoderChange"))["payload"] == {"device": dev, "position": 2, "ticks": -1}
            ctl.encoder_button(0, True)
            assert (await expect("encoderDown"))["payload"] == {"device": dev, "position": 0}
            ctl.encoder_button(0, False)
            assert (await expect("encoderUp"))["payload"] == {"device": dev, "position": 0}
            ctl.touch("down", 650, 400)
            ctl.touch("up")
            assert (await expect("touchscreenPress"))["payload"] == {"device": dev, "position": 3, "x": 50, "y": 20, "hold": False}
            ctl.touch("down", 210, 470)
            assert (await expect("touchscreenPress"))["payload"] == {"device": dev, "position": 1, "x": 10, "y": 90, "hold": True}
            ctl.touch("up")

            # unplug
            sim.terminate()
            assert (await expect("deregisterDevice"))["payload"] == dev
    finally:
        for proc in (plugin, sim):
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=5)


def test_plugin_against_mock_opendeck(tmp_path):
    if not SIM.exists() or not PLUGIN.exists():
        pytest.skip("build the simulator and the plugin first")
    asyncio.run(scenario(tmp_path))
