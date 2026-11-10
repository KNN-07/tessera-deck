"""Byte transports: the USB serial port of real hardware, or TCP to the simulator."""
from __future__ import annotations

import socket
from typing import List, Optional

from . import protocol


class Transport:
    def read(self) -> bytes:
        """Returns available bytes, or b"" after a short timeout. Raises OSError when closed."""
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class TcpTransport(Transport):
    def __init__(self, host: str, port: int):
        self._sock = socket.create_connection((host, port), timeout=5)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(0.1)

    def read(self) -> bytes:
        try:
            data = self._sock.recv(65536)
        except socket.timeout:
            return b""
        if not data:
            raise ConnectionError("simulator closed the connection")
        return data

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class SerialTransport(Transport):
    def __init__(self, port: str):
        import serial  # pyserial

        # The baud rate is ignored by USB CDC; DTR tells the deck a host is attached.
        self._ser = serial.Serial(port, 115200, timeout=0.1, write_timeout=5)
        self._ser.dtr = True

    def read(self) -> bytes:
        data = self._ser.read(1)
        if data and self._ser.in_waiting:
            data += self._ser.read(self._ser.in_waiting)
        return data

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def close(self) -> None:
        self._ser.close()


def find_serial_ports() -> List[str]:
    """Serial ports that look like a Tessera Deck."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    found = []
    for p in list_ports.comports():
        if (p.vid == protocol.USB_VID and p.pid == protocol.USB_PID) or (p.product and protocol.USB_PRODUCT in p.product):
            found.append(p.device)
    return sorted(found)


def open_transport(url: Optional[str] = None) -> Transport:
    """url: None (auto-detect), "tcp://host:port", or a serial port name such as /dev/ttyACM0 or COM5."""
    if url is None:
        ports = find_serial_ports()
        if not ports:
            raise FileNotFoundError("no Tessera Deck found; pass a port name or tcp://host:port")
        url = ports[0]
    if url.startswith("tcp://"):
        host, _, port = url[6:].rpartition(":")
        return TcpTransport(host or "127.0.0.1", int(port))
    return SerialTransport(url)
