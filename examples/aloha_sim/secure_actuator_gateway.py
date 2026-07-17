#!/usr/bin/env python3
"""Secure IoTAuth action receiver and isolated ALOHA simulation actuator."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import env as _env
import numpy as np
from openpi_client import actuator_control_rpc
from openpi_client import plain_action_transport
from openpi_client.secure_action_protocol import SecureActionProtocolError
from openpi_client.secure_action_protocol import decode_action_chunk

IOTAUTH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../iotauth/entity/python"))
if IOTAUTH_DIR not in sys.path:
    sys.path.append(IOTAUTH_DIR)

from iotauth import IoTAuthContext  # noqa: E402
from iotauth import IoTAuthError  # noqa: E402
from iotauth import SecureChannelClosed  # noqa: E402
from iotauth import SecureServer  # noqa: E402


@dataclass(frozen=True)
class QueuedActuatorAction:
    record_id: int
    observation_id: int
    motion_label: str
    monitor_start_ms: int
    action_index: int
    action: np.ndarray


class SecureActuatorGateway:
    """Merge ordered SIGA/INSIGA records and provide fail-closed execution."""

    def __init__(
        self,
        *,
        max_message_age_ms: int = 30_000,
        record_wait_timeout: float = 2.0,
        latency_log_path: str | None = None,
    ) -> None:
        self.max_message_age_ms = max_message_age_ms
        self.record_wait_timeout = record_wait_timeout
        self.next_expected_record_id = 0
        self.last_valid_executed_action: np.ndarray | None = None
        self._verified_actions: deque[QueuedActuatorAction] = deque()
        self._fatal_error: str | None = None
        self._condition = threading.Condition()
        self._latency_log_path = Path(latency_log_path) if latency_log_path else None
        self._latency_log_lock = threading.Lock()
        self._latency_record_ids: set[int] = set()
        if self._latency_log_path is not None:
            self._latency_log_path.parent.mkdir(parents=True, exist_ok=True)

    def ingest_payload(self, payload: bytes, *, source: str) -> None:
        started = time.perf_counter()
        try:
            message = decode_action_chunk(payload)
            if source == "insiga" and message.motion_label != "insiga":
                raise SecureActionProtocolError(
                    f"plaintext endpoint accepts only INSIGA records, got {message.motion_label!r}"
                )
            now_ms = int(time.time() * 1000)
            age_ms = now_ms - message.observation_timestamp_ms
            if age_ms < 0:
                raise SecureActionProtocolError("observation timestamp is in the future")
            if self.max_message_age_ms > 0 and age_ms > self.max_message_age_ms:
                raise SecureActionProtocolError(f"observation is stale: {age_ms} ms > {self.max_message_age_ms} ms")
            with self._condition:
                self._raise_if_fatal()
                if message.record_id != self.next_expected_record_id:
                    self._set_fatal(
                        "action record sequence violation: "
                        f"expected {self.next_expected_record_id}, received {message.record_id} "
                        f"from {source}"
                    )
                    self._raise_if_fatal()
                if self._verified_actions:
                    queued_id = self._verified_actions[0].record_id
                    self._set_fatal(f"record {message.record_id} arrived before queued record {queued_id} was consumed")
                    self._raise_if_fatal()
                executable = message.actions[: message.execution_horizon]
                self._verified_actions.extend(
                    QueuedActuatorAction(
                        record_id=message.record_id,
                        observation_id=message.observation_id,
                        motion_label=message.motion_label,
                        monitor_start_ms=message.monitor_start_ms,
                        action_index=action_index,
                        action=np.copy(action),
                    )
                    for action_index, action in enumerate(executable)
                )
                self.next_expected_record_id += 1
                self._condition.notify_all()

            elapsed_ms = (time.perf_counter() - started) * 1000
            print(
                "[SecureActuator] INGESTED "
                f"record={message.record_id} chunk={message.actions.shape} "
                f"label={message.motion_label.upper()} source={source} "
                f"execute={message.execution_horizon} age_ms={age_ms} ingest_ms={elapsed_ms:.2f}"
            )
        except Exception as exc:
            with self._condition:
                self._set_fatal(f"{source} action ingest failed: {exc}")
            print(f"[SecureActuator] FATAL: {exc}")
            raise

    def reset(self, current_state: np.ndarray) -> None:
        with self._condition:
            self._raise_if_fatal()
            self._verified_actions.clear()
            self.last_valid_executed_action = np.asarray(current_state, dtype=np.float32).copy()

    def next_action(self, expected_record_id: int) -> tuple[QueuedActuatorAction, bool]:
        deadline = time.monotonic() + self.record_wait_timeout
        with self._condition:
            while True:
                self._raise_if_fatal()
                if self._verified_actions:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._set_fatal(
                        f"missing action record {expected_record_id}: not received within "
                        f"{self.record_wait_timeout:.3f} seconds"
                    )
                    self._raise_if_fatal()
                self._condition.wait(remaining)

            if self._verified_actions:
                queued_action = self._verified_actions[0]
                if queued_action.record_id != expected_record_id:
                    self._set_fatal(
                        f"execution record mismatch: runtime expected {expected_record_id}, "
                        f"but actuator queued {queued_action.record_id}"
                    )
                    self._raise_if_fatal()
                self._verified_actions.popleft()
                self.last_valid_executed_action = np.copy(queued_action.action)
                return queued_action, True
            raise AssertionError("verified action queue unexpectedly empty")

    def record_driver_handoff(self, queued_action: QueuedActuatorAction, driver_handoff_ms: int) -> None:
        """Record latency once, when a record's first action reaches the driver boundary."""
        if queued_action.action_index != 0:
            return
        monitor_actuator_ms = driver_handoff_ms - queued_action.monitor_start_ms
        if monitor_actuator_ms < 0:
            raise RuntimeError(
                "monitor-actuator latency is negative; monitor and actuator clocks are not synchronized"
            )
        record = {
            "record_id": queued_action.record_id,
            "observation_id": queued_action.observation_id,
            "motion_label": queued_action.motion_label,
            "monitor_start_ms": queued_action.monitor_start_ms,
            "driver_handoff_ms": driver_handoff_ms,
            "monitor_actuator_ms": monitor_actuator_ms,
        }
        with self._latency_log_lock:
            if queued_action.record_id in self._latency_record_ids:
                raise RuntimeError(f"duplicate driver handoff for record {queued_action.record_id}")
            self._latency_record_ids.add(queued_action.record_id)
            if self._latency_log_path is not None:
                with self._latency_log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
        print(
            "[SecureActuator] DRIVER_HANDOFF "
            f"record={queued_action.record_id} label={queued_action.motion_label.upper()} "
            f"monitor_actuator_ms={monitor_actuator_ms}"
        )

    def _set_fatal(self, message: str) -> None:
        if self._fatal_error is None:
            self._fatal_error = message
            self._verified_actions.clear()
            self._condition.notify_all()

    def _raise_if_fatal(self) -> None:
        if self._fatal_error is not None:
            raise RuntimeError(f"actuator is fail-closed: {self._fatal_error}")


class ActuatorControlServer:
    """Own Gym and expose observation/tick operations to the existing runtime."""

    def __init__(self, gateway: SecureActuatorGateway, args: argparse.Namespace) -> None:
        self.gateway = gateway
        self.environment = _env.AlohaSimEnvironment(
            task=args.task,
            seed=args.seed,
            max_episode_steps=args.max_episode_steps,
        )

    def serve(self, host: str, port: int) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.listen(1)
            print(f"[SecureActuator] Control RPC listening on {host}:{port}")
            while True:
                connection, _address = listener.accept()
                with connection:
                    while True:
                        try:
                            request = actuator_control_rpc.receive_message(connection)
                        except ConnectionError:
                            break
                        try:
                            response = self._handle(request)
                        except Exception as exc:
                            response = {"ok": False, "error": str(exc)}
                        try:
                            actuator_control_rpc.send_message(connection, response)
                        except (ConnectionError, OSError):
                            break

    def _handle(self, request: dict) -> dict:
        command = request.get("command")
        if command == "reset":
            self.environment.reset()
            self.gateway.reset(self.environment.get_observation()["state"])
            return {"ok": True}
        if command == "get_observation":
            return {"ok": True, "observation": self.environment.get_observation()}
        if command == "is_episode_complete":
            return {"ok": True, "done": self.environment.is_episode_complete()}
        if command == "apply_tick":
            expected_record_id = request.get("expected_record_id")
            if not isinstance(expected_record_id, int) or isinstance(expected_record_id, bool):
                raise ValueError("apply_tick requires an integer expected_record_id")
            queued_action, from_verified_queue = self.gateway.next_action(expected_record_id)
            driver_handoff_ms = int(time.time() * 1000)
            self.environment.apply_action({"actions": queued_action.action})
            self.gateway.record_driver_handoff(queued_action, driver_handoff_ms)
            return {"ok": True, "verified_action": from_verified_queue}
        raise ValueError(f"unknown actuator control command: {command!r}")


def _serve_secure_channels(ctx, gateway: SecureActuatorGateway, args: argparse.Namespace) -> None:
    with SecureServer(ctx, host=args.host, port=args.port, timeout=args.timeout) as server:
        print(f"[SecureActuator] Secure channel listening on {args.host}:{args.port}")
        while True:
            try:
                channel = server.serve_once()
                print(f"[SecureActuator] Secure channel established with key {channel.session_key.id.hex()}")
                while True:
                    payload = channel.recv()
                    gateway.ingest_payload(payload, source="siga")
            except SecureChannelClosed:
                print("[SecureActuator] Secure channel closed; waiting for key rotation/reconnect")
            except IoTAuthError as exc:
                print(f"[SecureActuator] IoTAuth error: {exc}; waiting for a new connection")
            except Exception as exc:
                # Keep the safety service available after a malformed or abruptly
                # disconnected peer. The gateway remains fail-closed after ingest errors.
                print(f"[SecureActuator] Channel error: {exc}; waiting for a new connection")


def _serve_insiga_channels(gateway: SecureActuatorGateway, args: argparse.Namespace) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.insiga_host, args.insiga_port))
        listener.listen(5)
        print(f"[SecureActuator] INSIGA plaintext listener on {args.insiga_host}:{args.insiga_port}")
        while True:
            connection, _address = listener.accept()
            with connection:
                while True:
                    try:
                        payload = plain_action_transport.receive_payload(connection)
                        gateway.ingest_payload(payload, source="insiga")
                    except ConnectionError:
                        break
                    except Exception as exc:
                        print(f"[SecureActuator] INSIGA connection terminated: {exc}")
                        break


def serve(args: argparse.Namespace) -> None:
    # Generated JSON configs contain paths relative to their own directory.
    config_file = os.path.abspath(args.config_file)
    original_cwd = os.getcwd()
    try:
        os.chdir(os.path.dirname(config_file))
        ctx = IoTAuthContext.from_config(config_file)
    finally:
        os.chdir(original_cwd)
    gateway = SecureActuatorGateway(
        max_message_age_ms=args.max_message_age_ms,
        record_wait_timeout=args.record_wait_timeout,
        latency_log_path=args.latency_log,
    )
    threading.Thread(
        target=_serve_secure_channels,
        args=(ctx, gateway, args),
        daemon=True,
    ).start()
    threading.Thread(
        target=_serve_insiga_channels,
        args=(gateway, args),
        daemon=True,
    ).start()
    ActuatorControlServer(gateway, args).serve(args.control_host, args.control_port)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenPI secure actuator service")
    parser.add_argument("--config-file", required=True, help="Registered actuator/server entity config")
    parser.add_argument("--host", default=os.environ.get("SECURE_ACTUATOR_BIND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SECURE_ACTUATOR_PORT", "21100")))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--insiga-host",
        default=os.environ.get("INSIGA_ACTUATOR_BIND_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--insiga-port",
        type=int,
        default=int(os.environ.get("INSIGA_ACTUATOR_PORT", "21102")),
    )
    parser.add_argument(
        "--control-host",
        default=os.environ.get("SECURE_ACTUATOR_CONTROL_BIND_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=int(os.environ.get("SECURE_ACTUATOR_CONTROL_PORT", "21101")),
    )
    parser.add_argument("--task", default="gym_aloha/AlohaTransferCube-v0")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--max-episode-steps",
        type=int,
        default=int(os.environ.get("ALOHA_MAX_EPISODE_STEPS", "0") or 0),
    )
    parser.add_argument(
        "--max-message-age-ms",
        type=int,
        default=int(os.environ.get("SECURE_ACTION_MAX_AGE_MS", "30000")),
    )
    parser.add_argument(
        "--record-wait-timeout",
        type=float,
        default=float(os.environ.get("ACTUATOR_RECORD_WAIT_TIMEOUT", "2.0")),
    )
    parser.add_argument(
        "--latency-log",
        default=os.environ.get("ACTUATOR_LATENCY_LOG") or None,
        help="Optional JSONL path for one monitor_actuator_ms record per action chunk.",
    )
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
