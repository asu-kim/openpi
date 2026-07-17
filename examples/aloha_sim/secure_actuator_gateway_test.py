import json
import time

import numpy as np
from openpi_client.secure_action_protocol import encode_action_chunk
import pytest
from secure_actuator_gateway import SecureActuatorGateway


def payload(record_id, *, label="insiga", horizon=10, monitor_start_ms=None):
    if monitor_start_ms is None:
        monitor_start_ms = int(time.time() * 1000)
    return encode_action_chunk(
        np.full((32, 14), record_id, dtype=np.float32),
        record_id=record_id,
        observation_id=record_id,
        observation_timestamp_ms=int(time.time() * 1000),
        monitor_start_ms=monitor_start_ms,
        execution_horizon=horizon,
        motion_label=label,
    )


def test_insiga_record_is_consumed_in_order():
    gateway = SecureActuatorGateway(record_wait_timeout=0.01)
    gateway.reset(np.zeros(14, dtype=np.float32))
    gateway.ingest_payload(payload(0), source="insiga")

    for _ in range(10):
        queued_action, verified = gateway.next_action(0)
        assert verified
        np.testing.assert_array_equal(queued_action.action, np.zeros(14, dtype=np.float32))


def test_skipped_record_enters_fatal_state():
    gateway = SecureActuatorGateway(record_wait_timeout=0.01)
    gateway.reset(np.zeros(14, dtype=np.float32))

    with pytest.raises(RuntimeError, match="expected 0, received 1"):
        gateway.ingest_payload(payload(1), source="insiga")
    with pytest.raises(RuntimeError, match="fail-closed"):
        gateway.next_action(0)


def test_missing_record_times_out_fail_closed():
    gateway = SecureActuatorGateway(record_wait_timeout=0.001)
    gateway.reset(np.zeros(14, dtype=np.float32))

    with pytest.raises(RuntimeError, match="missing action record 0"):
        gateway.next_action(0)


def test_latency_is_recorded_once_for_first_driver_handoff(tmp_path):
    latency_log = tmp_path / "monitor_actuator.jsonl"
    gateway = SecureActuatorGateway(record_wait_timeout=0.01, latency_log_path=str(latency_log))
    gateway.reset(np.zeros(14, dtype=np.float32))
    gateway.ingest_payload(payload(0, monitor_start_ms=1_000), source="insiga")

    first_action, _ = gateway.next_action(0)
    gateway.record_driver_handoff(first_action, driver_handoff_ms=1_025)
    second_action, _ = gateway.next_action(0)
    gateway.record_driver_handoff(second_action, driver_handoff_ms=1_026)

    records = [json.loads(line) for line in latency_log.read_text().splitlines()]
    assert records == [
        {
            "record_id": 0,
            "observation_id": 0,
            "motion_label": "insiga",
            "monitor_start_ms": 1_000,
            "driver_handoff_ms": 1_025,
            "monitor_actuator_ms": 25,
        }
    ]
