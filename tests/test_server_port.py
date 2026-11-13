import socket

import pytest

from backend.serve import bind_available_port


def test_occupied_port_is_skipped_and_selected_port_stays_reserved():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        requested = occupied.getsockname()[1]
        with bind_available_port(requested) as selected:
            assert selected.getsockname()[1] > requested
            with socket.socket() as contender:
                with pytest.raises(OSError):
                    contender.bind(selected.getsockname())


def test_available_port_is_reused_after_listener_closes():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with bind_available_port(port) as selected:
        assert selected.getsockname()[1] == port


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_is_rejected(port):
    with pytest.raises(ValueError, match="Port must be between"):
        bind_available_port(port)
