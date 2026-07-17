"""Wire messages carried by the SIGA and INSIGA actuator data channels."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import numpy as np

from openpi_client import msgpack_numpy


PROTOCOL_VERSION = 2
ACTION_CHUNK = "action_chunk"
MAX_ACTION_TIMESTEPS = 256
MAX_ACTION_DIMS = 256


class SecureActionProtocolError(ValueError):
    """Raised when an actuator-channel message does not match the schema."""


@dataclass(frozen=True)
class ActionChunkMessage:
    record_id: int
    observation_id: int
    observation_timestamp_ms: int
    execution_horizon: int
    motion_label: str
    actions: np.ndarray


def encode_action_chunk(
    actions: np.ndarray,
    *,
    record_id: int,
    observation_id: int,
    observation_timestamp_ms: int | None = None,
    execution_horizon: int,
    motion_label: str,
) -> bytes:
    array = _validate_actions(actions, execution_horizon)
    if observation_timestamp_ms is None:
        observation_timestamp_ms = int(time.time() * 1000)
    message = {
        "version": PROTOCOL_VERSION,
        "message_type": ACTION_CHUNK,
        "record_id": _nonnegative_int("record_id", record_id),
        "observation_id": _nonnegative_int("observation_id", observation_id),
        "observation_timestamp_ms": _nonnegative_int("observation_timestamp_ms", observation_timestamp_ms),
        "execution_horizon": int(execution_horizon),
        "motion_label": _motion_label(motion_label),
        "actions": array,
    }
    return msgpack_numpy.packb(message, use_bin_type=True)


def decode_action_chunk(data: bytes) -> ActionChunkMessage:
    message = _unpack_mapping(data)
    _require_header(message, ACTION_CHUNK)
    required = {
        "record_id",
        "observation_id",
        "observation_timestamp_ms",
        "execution_horizon",
        "motion_label",
        "actions",
    }
    _require_fields(message, required)
    horizon = _positive_int("execution_horizon", message["execution_horizon"])
    actions = _validate_actions(message["actions"], horizon)
    return ActionChunkMessage(
        record_id=_nonnegative_int("record_id", message["record_id"]),
        observation_id=_nonnegative_int("observation_id", message["observation_id"]),
        observation_timestamp_ms=_nonnegative_int("observation_timestamp_ms", message["observation_timestamp_ms"]),
        execution_horizon=horizon,
        motion_label=_motion_label(message["motion_label"]),
        actions=actions,
    )


def _validate_actions(actions: Any, execution_horizon: int) -> np.ndarray:
    horizon = _positive_int("execution_horizon", execution_horizon)
    try:
        array = np.asarray(actions, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise SecureActionProtocolError(f"actions must be numeric: {exc}") from exc
    if array.ndim != 2:
        raise SecureActionProtocolError(f"actions must be a 2D [T, D] array, got {array.shape}")
    timesteps, dims = array.shape
    if not 1 <= timesteps <= MAX_ACTION_TIMESTEPS:
        raise SecureActionProtocolError(f"action timestep count out of range: {timesteps}")
    if not 1 <= dims <= MAX_ACTION_DIMS:
        raise SecureActionProtocolError(f"action dimension count out of range: {dims}")
    if horizon > timesteps:
        raise SecureActionProtocolError(f"execution_horizon {horizon} exceeds chunk length {timesteps}")
    if not np.isfinite(array).all():
        raise SecureActionProtocolError("actions contain NaN or infinity")
    return np.ascontiguousarray(array)


def _unpack_mapping(data: bytes) -> dict[str, Any]:
    try:
        message = msgpack_numpy.unpackb(data, raw=False)
    except Exception as exc:
        raise SecureActionProtocolError(f"invalid msgpack payload: {exc}") from exc
    if not isinstance(message, dict):
        raise SecureActionProtocolError("message must be a mapping")
    return message


def _require_header(message: dict[str, Any], expected_type: str) -> None:
    if message.get("version") != PROTOCOL_VERSION:
        raise SecureActionProtocolError(f"unsupported protocol version: {message.get('version')}")
    if message.get("message_type") != expected_type:
        raise SecureActionProtocolError(f"expected message_type {expected_type!r}, got {message.get('message_type')!r}")


def _require_fields(message: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required.difference(message))
    if missing:
        raise SecureActionProtocolError(f"missing required fields: {', '.join(missing)}")


def _nonnegative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise SecureActionProtocolError(f"{name} must be an integer")
    result = int(value)
    if result < 0:
        raise SecureActionProtocolError(f"{name} must be non-negative")
    return result


def _positive_int(name: str, value: Any) -> int:
    result = _nonnegative_int(name, value)
    if result < 1:
        raise SecureActionProtocolError(f"{name} must be positive")
    return result


def _motion_label(value: Any) -> str:
    if not isinstance(value, str) or value not in {"insiga", "siga"}:
        raise SecureActionProtocolError("motion_label must be 'insiga' or 'siga'")
    return value
