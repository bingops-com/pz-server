#!/usr/bin/env python3
import argparse
import socket
import struct
import sys


def packet(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def receive(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", sock.recv(4))[0]
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("RCON connection closed")
        data += chunk
    request_id, packet_type = struct.unpack("<ii", data[:8])
    return request_id, packet_type, data[8:-2].decode(errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--password", required=True)
    parser.add_argument("command")
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        sock.sendall(packet(1, 3, args.password))
        authenticated = False
        for _ in range(2):
            auth_id, auth_type, _ = receive(sock)
            if auth_id == -1:
                print("RCON authentication failed", file=sys.stderr)
                return 1
            if auth_id == 1 and auth_type == 2:
                authenticated = True
                break
        if not authenticated:
            print("Unexpected RCON authentication response", file=sys.stderr)
            return 1
        sock.sendall(packet(2, 2, args.command))
        response_id, _, response = receive(sock)
        if response_id != 2:
            print("Unexpected RCON response", file=sys.stderr)
            return 1
        if response:
            print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
