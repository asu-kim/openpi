"""Monitor-side client for the separately running secure actuator gateway."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from openpi_client.secure_action_protocol import encode_action_chunk


class SecureActuatorClient:
    """Send serialized SIGA chunks over an IoTAuth SecureChannel."""

    def __init__(
        self,
        ctx: Any,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.ctx = ctx
        self.host = host
        self.port = port
        self.timeout = timeout
        self._channel: Any | None = None
        self._channel_key_id: bytes | None = None

    def send_actions(
        self,
        actions: np.ndarray,
        *,
        session_key: Any,
        record_id: int,
        observation_id: int,
        observation_timestamp_ms: int,
        execution_horizon: int,
        motion_label: str = "siga",
    ) -> dict[str, float]:
        connect_start = time.perf_counter()
        self._ensure_channel(session_key)
        connect_ms = (time.perf_counter() - connect_start) * 1000

        serialize_start = time.perf_counter()
        payload = encode_action_chunk(
            actions,
            record_id=record_id,
            observation_id=observation_id,
            observation_timestamp_ms=observation_timestamp_ms,
            execution_horizon=execution_horizon,
            motion_label=motion_label,
        )
        serialize_ms = (time.perf_counter() - serialize_start) * 1000

        send_start = time.perf_counter()
        try:
            self._channel.send(payload)
        except Exception:
            self.close()
            raise
        return {
            "secure_connect_ms": connect_ms,
            "secure_serialize_ms": serialize_ms,
            "secure_send_ms": (time.perf_counter() - send_start) * 1000,
        }

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
        self._channel = None
        self._channel_key_id = None

    def _ensure_channel(self, session_key: Any) -> None:
        key_id = bytes(session_key.id)
        if self._channel is not None and not self._channel.closed and self._channel_key_id == key_id:
            return
        self.close()
        self._channel = self.ctx.connect_secure(
            key=session_key,
            host=self.host,
            port=self.port,
            timeout=self.timeout,
        )
        if self.timeout is not None and hasattr(self._channel.socket, "settimeout"):
            self._channel.socket.settimeout(self.timeout)
        self._channel_key_id = key_id
