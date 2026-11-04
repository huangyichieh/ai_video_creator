import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from story_channel.windows_asyncio import _wrap_connection_lost, install_windows_transport_cleanup

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows asyncio transport compatibility")


def reset(code=10054):
    error = ConnectionResetError("Connection reset")
    error.winerror = code
    return error


def transport():
    return SimpleNamespace(_called_connection_lost=False,
                           _protocol=Mock(), _sock=Mock(), _server=Mock())


def original_cleanup():
    # Exercise this Python installation's real cleanup code without a network.
    from asyncio.proactor_events import _ProactorBasePipeTransport
    callback = _ProactorBasePipeTransport._call_connection_lost
    while hasattr(callback, "__wrapped__"):
        callback = callback.__wrapped__
    return callback


def test_reset_during_shutdown_finishes_close_and_detach_once():
    target = transport()
    sock, server, protocol = target._sock, target._server, target._protocol
    def cleanup_with_builtin_shutdown_error(transport, exc):
        # A native socket error has no extra Python frame after the cleanup
        # frame; Mock.side_effect would add unittest.mock traceback frames.
        if transport._called_connection_lost:
            return
        transport._protocol.connection_lost(exc)
        raise reset()
    callback = _wrap_connection_lost(cleanup_with_builtin_shutdown_error)
    callback(target, None)
    callback(target, None)
    sock.close.assert_called_once()
    protocol.connection_lost.assert_called_once_with(None)
    server._detach.assert_called_once_with(target)
    assert target._sock is None and target._server is None
    assert target._called_connection_lost


def test_normal_cleanup_is_unchanged():
    target = transport()
    sock = target._sock
    _wrap_connection_lost(original_cleanup())(target, None)
    sock.shutdown.assert_called_once()
    sock.close.assert_called_once()
    assert target._called_connection_lost


def test_other_shutdown_errors_are_not_hidden():
    target = transport()
    error = reset(10053)
    target._sock.shutdown.side_effect = error
    with pytest.raises(ConnectionResetError) as caught:
        _wrap_connection_lost(original_cleanup())(target, None)
    assert caught.value is error


def test_protocol_errors_are_not_hidden():
    target = transport()
    error = reset()
    target._protocol.connection_lost.side_effect = error
    with pytest.raises(ConnectionResetError) as caught:
        _wrap_connection_lost(original_cleanup())(target, None)
    assert caught.value is error
    assert target._called_connection_lost  # Original finally still closed it.


def test_installation_is_idempotent(monkeypatch):
    from asyncio.proactor_events import _ProactorBasePipeTransport
    monkeypatch.setattr(_ProactorBasePipeTransport, "_call_connection_lost", original_cleanup())
    install_windows_transport_cleanup()
    callback = _ProactorBasePipeTransport._call_connection_lost
    install_windows_transport_cleanup()
    assert _ProactorBasePipeTransport._call_connection_lost is callback
