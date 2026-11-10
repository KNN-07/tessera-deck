"""Control channel of the simulator: presses keys, turns dials, touches the strip."""
from __future__ import annotations

import socket


class SimControl:
    def __init__(self, host: str = "127.0.0.1", port: int = 47801):
        self._sock = socket.create_connection((host, port), timeout=5)
        self._file = self._sock.makefile("rw", newline="\n")

    def cmd(self, line: str) -> str:
        self._file.write(line + "\n")
        self._file.flush()
        reply = self._file.readline().strip()
        if not reply.startswith("ok"):
            raise RuntimeError(f"simulator: {reply or 'no reply'} (command: {line})")
        return reply[2:].strip()

    def close(self) -> None:
        self._file.close()
        self._sock.close()

    def key(self, key: int, down: bool) -> None:
        self.cmd(f"key {key} {'down' if down else 'up'}")

    def encoder_button(self, encoder: int, down: bool) -> None:
        self.cmd(f"encbtn {encoder} {'down' if down else 'up'}")

    def turn(self, encoder: int, detents: int) -> None:
        self.cmd(f"enc {encoder} {detents}")

    def touch(self, action: str, x: int = 0, y: int = 0) -> None:
        """action: down, move or up; x, y in panel pixels."""
        self.cmd(f"touch {action} {x} {y}")

    def advance(self, ms: int) -> None:
        """Advances virtual time (simulator started with --virtual-time)."""
        self.cmd(f"advance {ms}")

    def screenshot(self, path: str) -> None:
        """Writes the scanned-out screen as a PPM file."""
        self.cmd(f"screenshot {path}")

    def backlight(self):
        state, pct = self.cmd("backlight").split()
        return state == "on", int(pct)

    def suspend(self, on: bool) -> None:
        self.cmd(f"suspend {'on' if on else 'off'}")

    def reboots(self):
        count, mode = self.cmd("reboots").split()
        return int(count), int(mode)
