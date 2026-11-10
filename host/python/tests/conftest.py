"""Fixtures that run the firmware simulator. Build it first:

    cmake -S firmware -B firmware/build && cmake --build firmware/build
"""
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tessera import Deck  # noqa: E402
from tessera.simctl import SimControl  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SIM = pathlib.Path(os.environ.get("TESSERA_SIM", REPO / "firmware" / "build" / "tessera-sim"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sim:
    def __init__(self, state_dir, virtual_time=True):
        if not SIM.exists():
            pytest.skip(f"simulator not built ({SIM})")
        self.port, self.ctl_port = free_port(), free_port()
        args = [str(SIM), "--port", str(self.port), "--ctl", str(self.ctl_port), "--state", str(state_dir)]
        if virtual_time:
            args.append("--virtual-time")
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE)
        self.proc.stdout.readline()  # banner = sockets are listening
        self.ctl = SimControl(port=self.ctl_port)
        self.url = f"tcp://127.0.0.1:{self.port}"

    def deck(self, **kwargs) -> Deck:
        return Deck.open(self.url, **kwargs)

    def stop(self):
        self.ctl.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)

    def press(self, key, hold_ms=20):
        self.ctl.key(key, True)
        self.ctl.advance(hold_ms)
        self.ctl.key(key, False)
        self.ctl.advance(20)

    def screenshot(self, tmp_path):
        from PIL import Image

        path = tmp_path / f"shot-{time.monotonic_ns()}.ppm"
        self.ctl.screenshot(str(path))
        return Image.open(path).convert("RGB")


@pytest.fixture
def sim(tmp_path):
    s = Sim(tmp_path / "state")
    yield s
    s.stop()


@pytest.fixture
def deck(sim):
    d = sim.deck()
    yield d
    d.close()
