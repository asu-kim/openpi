"""Length-prefixed plaintext transport for serialized INSIGA action chunks."""

from __future__ import annotations

import socket
import struct


HEADER_SIZE = 4
MAX_PAYLOAD_SIZE = 32 * 1024 * 1024


def send_payload(sock: socket.socket, payload: bytes) -> None:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not payload or len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(f"plaintext action payload size is invalid: {len(payload)} bytes")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def receive_payload(sock: socket.socket) -> bytes:
    size = struct.unpack("!I", _receive_exact(sock, HEADER_SIZE))[0]
    if not size or size > MAX_PAYLOAD_SIZE:
        raise ValueError(f"plaintext action payload size is invalid: {size} bytes")
    return _receive_exact(sock, size)


def _receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("plaintext action connection closed")
        chunks.extend(chunk)
    return bytes(chunks)
