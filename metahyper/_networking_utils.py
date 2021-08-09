import socket

import dill


def make_request(host, port, data, receive_something=False, timeout_seconds=None):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_seconds)

        sock.connect((host, port))
        sock.sendall(dill.dumps(data))

        if receive_something:
            received = dill.loads(sock.recv(1024))
            return received
