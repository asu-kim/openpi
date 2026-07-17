"""Monitor-side plaintext client for serialized INSIGA action chunks."""

from __future__ import annotations

import socket
import time

import numpy as np
from openpi_client import plain_action_transport
from openpi_client.secure_action_protocol import encode_action_chunk


class InsigaActuatorClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 21102, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None

    def send_actions(
        self,
        actions: np.ndarray,
        *,
        record_id: int,
        observation_id: int,
        observation_timestamp_ms: int,
        execution_horizon: int,
    ) -> dict[str, float]:
        connect_started = time.perf_counter()
        self._ensure_connection()
        connect_ms = (time.perf_counter() - connect_started) * 1000

        serialize_started = time.perf_counter()
        payload = encode_action_chunk(
            actions,
            record_id=record_id,
            observation_id=observation_id,
            observation_timestamp_ms=observation_timestamp_ms,
            execution_horizon=execution_horizon,
            motion_label="insiga",
        )
        serialize_ms = (time.perf_counter() - serialize_started) * 1000

        send_started = time.perf_counter()
        try:
            plain_action_transport.send_payload(self._socket, payload)
        except Exception:
            self.close()
            raise
        return {
            "insiga_connect_ms": connect_ms,
            "insiga_serialize_ms": serialize_ms,
            "insiga_send_ms": (time.perf_counter() - send_started) * 1000,
        }

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = None

    def _ensure_connection(self) -> None:
        if self._socket is not None:
            return
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._socket.settimeout(self.timeout)
