"""Runtime-side proxy for an ALOHA environment owned by the secure actuator process."""

from __future__ import annotations

import socket
import time

from openpi_client import actuator_control_rpc
from openpi_client.runtime import environment as _environment
from typing_extensions import override


class SecureRemoteAlohaEnvironment(_environment.Environment):
    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._socket = socket.create_connection((host, port), timeout=min(timeout, 1.0))
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        self._socket.settimeout(timeout)

    @override
    def reset(self) -> None:
        self._request("reset")

    @override
    def is_episode_complete(self) -> bool:
        return bool(self._request("is_episode_complete")["done"])

    @override
    def get_observation(self) -> dict:
        return self._request("get_observation")["observation"]

    @override
    def apply_action(self, action: dict) -> None:
        # The plaintext policy action is deliberately ignored. The isolated actuator
        # executes only actions that arrived through its verified secure queue.
        expected_record_id = int(action.get("action_record_id", -1))
        self._request("apply_tick", expected_record_id=expected_record_id)

    def _request(self, command: str, **fields) -> dict:
        actuator_control_rpc.send_message(self._socket, {"command": command, **fields})
        response = actuator_control_rpc.receive_message(self._socket)
        if not response.get("ok"):
            raise RuntimeError(f"Secure actuator control error: {response.get('error', 'unknown error')}")
        return response
