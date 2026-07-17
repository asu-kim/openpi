import socket

from openpi_client import plain_action_transport


def test_plaintext_payload_round_trip():
    sender, receiver = socket.socketpair()
    try:
        plain_action_transport.send_payload(sender, b"serialized-insiga")
        assert plain_action_transport.receive_payload(receiver) == b"serialized-insiga"
    finally:
        sender.close()
        receiver.close()
