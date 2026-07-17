import time

import numpy as np
import pytest

from openpi_client import secure_action_protocol as protocol


def test_action_chunk_round_trip_preserves_full_chunk_and_execution_horizon():
    actions = np.arange(32 * 14, dtype=np.float32).reshape(32, 14)

    payload = protocol.encode_action_chunk(
        actions,
        record_id=7,
        observation_id=11,
        observation_timestamp_ms=1234,
        execution_horizon=10,
        motion_label="siga",
    )
    decoded = protocol.decode_action_chunk(payload)

    assert decoded.record_id == 7
    assert decoded.observation_id == 11
    assert decoded.observation_timestamp_ms == 1234
    assert decoded.execution_horizon == 10
    assert decoded.motion_label == "siga"
    np.testing.assert_array_equal(decoded.actions, actions)


def test_action_chunk_rejects_horizon_larger_than_chunk():
    with pytest.raises(protocol.SecureActionProtocolError, match="exceeds chunk length"):
        protocol.encode_action_chunk(
            np.zeros((10, 14), dtype=np.float32),
            record_id=0,
            observation_id=0,
            execution_horizon=11,
            motion_label="siga",
        )


def test_action_chunk_rejects_nonfinite_values():
    actions = np.zeros((10, 14), dtype=np.float32)
    actions[3, 2] = np.nan
    with pytest.raises(protocol.SecureActionProtocolError, match="NaN or infinity"):
        protocol.encode_action_chunk(
            actions,
            record_id=0,
            observation_id=0,
            execution_horizon=10,
            motion_label="siga",
        )


def test_action_chunk_rejects_unknown_motion_label():
    with pytest.raises(protocol.SecureActionProtocolError, match="motion_label"):
        protocol.encode_action_chunk(
            np.zeros((10, 14), dtype=np.float32),
            record_id=0,
            observation_id=0,
            execution_horizon=10,
            motion_label="still",
        )


def test_default_timestamp_is_current_time():
    before = int(time.time() * 1000)
    payload = protocol.encode_action_chunk(
        np.zeros((10, 14), dtype=np.float32),
        record_id=0,
        observation_id=0,
        execution_horizon=10,
        motion_label="insiga",
    )
    after = int(time.time() * 1000)

    decoded = protocol.decode_action_chunk(payload)
    assert before <= decoded.observation_timestamp_ms <= after
