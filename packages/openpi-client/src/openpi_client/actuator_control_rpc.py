"""Length-prefixed local RPC used by the isolated ALOHA actuator process."""

from __future__ import annotations

import socket
import struct
from typing import Any

from openpi_client import msgpack_numpy


HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 32 * 1024 * 1024


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    payload = msgpack_numpy.packb(message, use_bin_type=True)
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ValueError(f"RPC message is too large: {len(payload)} bytes")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def receive_message(sock: socket.socket) -> dict[str, Any]:
    size = struct.unpack("!I", _receive_exact(sock, HEADER_SIZE))[0]
    if size > MAX_MESSAGE_SIZE:
        raise ValueError(f"RPC message is too large: {size} bytes")
    message = msgpack_numpy.unpackb(_receive_exact(sock, size), raw=False)
    if not isinstance(message, dict):
        raise ValueError("RPC message must be a mapping")
    return message


def _receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("RPC connection closed")
        chunks.extend(chunk)
    return bytes(chunks)
