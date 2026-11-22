#!/usr/bin/env python3
"""Read-only A2S_INFO check; does not print player names or game data."""
import argparse
import json
import socket
import time


def query(host, port, timeout):
    started = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(timeout)
        connection.connect((host, port))
        challenge = b''
        for _ in range(3):
            connection.send(b'\xff' * 4 + b'TSource Engine Query\x00' + challenge)
            value = connection.recv(4096)
            if value[:5] == b'\xff' * 4 + b'A' and len(value) >= 9:
                challenge = value[5:9]
                continue
            if value[:5] != b'\xff' * 4 + b'I':
                raise ValueError('Unexpected A2S reply')
            payload = value[6:]
            for _ in range(4):
                _, separator, payload = payload.partition(b'\x00')
                if not separator:
                    raise ValueError('Incomplete A2S reply')
            if len(payload) < 9:
                raise ValueError('Incomplete A2S statistics')
            version, separator, _ = payload[9:].partition(b'\x00')
            if not separator:
                raise ValueError('Incomplete A2S version')
            return {'online': True, 'players': payload[2], 'maxPlayers': payload[3],
                    'queryVersion': version.decode('utf-8', errors='replace'),
                    'latencyMs': round((time.monotonic() - started) * 1000)}
    raise ValueError('A2S challenge did not complete')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host')
    parser.add_argument('port', type=int)
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    try:
        result = query(args.host, args.port, args.timeout)
    except (OSError, ValueError):
        print(json.dumps({'online': False, 'endpoint': f'{args.host}:{args.port}'}))
        raise SystemExit(1)
    print(json.dumps(dict(result, endpoint=f'{args.host}:{args.port}')))
