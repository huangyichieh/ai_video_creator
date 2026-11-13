"""Complete Windows transport cleanup when shutdown meets an already-reset peer."""
from functools import wraps
import sys


def _wrap_connection_lost(original):
    @wraps(original)
    def connection_lost(transport, exc):
        try:
            return original(transport, exc)
        except ConnectionResetError as error:
            traceback = error.__traceback__
            while traceback.tb_next is not None:
                traceback = traceback.tb_next
            # Only the reset raised inside asyncio's cleanup itself qualifies.
            # Exceptions from a protocol or a provider must still propagate.
            if getattr(error, "winerror", None) != 10054 or traceback.tb_frame.f_code is not original.__code__:
                raise
            sock = transport._sock
            if sock is None:
                raise
            # CPython's finally block stops at sock.shutdown() on this error,
            # leaving close/detach undone. Finish that exact remainder once.
            sock.close()
            transport._sock = None
            server = transport._server
            if server is not None:
                server._detach(transport)
                transport._server = None
            transport._called_connection_lost = True

    connection_lost._story_windows_cleanup = True
    return connection_lost


def install_windows_transport_cleanup():
    if sys.platform != "win32":
        return
    from asyncio.proactor_events import _ProactorBasePipeTransport
    original = _ProactorBasePipeTransport._call_connection_lost
    if not getattr(original, "_story_windows_cleanup", False):
        _ProactorBasePipeTransport._call_connection_lost = _wrap_connection_lost(original)
