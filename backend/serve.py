"""Start the local server on the first available port, retaining the bound socket."""

import argparse
import errno
import socket

import uvicorn


def bind_available_port(port: int) -> socket.socket:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    for candidate in range(port, 65536):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", candidate))
            listener.listen(128)
            return listener
        except OSError as error:
            listener.close()
            if error.errno not in (errno.EADDRINUSE, errno.EACCES) and getattr(
                error, "winerror", None
            ) not in (10048, 10013):
                raise
    raise OSError(f"No available local port found from {port} to 65535.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with bind_available_port(args.port) as listener:
        port = listener.getsockname()[1]
        if port != args.port:
            print(f"Port {args.port} is unavailable; using port {port}.", flush=True)
        print(f"Story Studio: http://127.0.0.1:{port}", flush=True)
        config = uvicorn.Config("backend.main:app", host="127.0.0.1", port=port)
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
