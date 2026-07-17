from types import SimpleNamespace

import numpy as np
from openpi_client.secure_action_protocol import decode_action_chunk

from secure_actuator_client import SecureActuatorClient


class FakeChannel:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.socket = SimpleNamespace(settimeout=lambda _timeout: None)

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.channels = []

    def connect_secure(self, **_kwargs):
        channel = FakeChannel()
        self.channels.append(channel)
        return channel


def key(key_id):
    return SimpleNamespace(id=key_id)


def test_client_reuses_channel_for_same_key_and_rotates_for_new_key():
    context = FakeContext()
    client = SecureActuatorClient(context, host="localhost", port=21100)
    actions = np.zeros((32, 14), dtype=np.float32)

    timing = client.send_actions(
        actions,
        session_key=key(b"key-one"),
        record_id=0,
        observation_id=0,
        observation_timestamp_ms=1,
        execution_horizon=10,
    )
    client.send_actions(
        actions,
        session_key=key(b"key-one"),
        record_id=1,
        observation_id=1,
        observation_timestamp_ms=2,
        execution_horizon=10,
    )
    client.send_actions(
        actions,
        session_key=key(b"key-two"),
        record_id=2,
        observation_id=2,
        observation_timestamp_ms=3,
        execution_horizon=10,
    )

    assert timing["secure_send_ms"] >= 0
    assert len(context.channels) == 2
    assert context.channels[0].closed
    assert not context.channels[1].closed
    assert decode_action_chunk(context.channels[1].sent[-1]).motion_label == "siga"
