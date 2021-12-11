import atexit
import fcntl
import logging
import random
import socket
import socketserver

import dill
import netifaces

from metahyper.status import load_state

logger = logging.getLogger(__name__)


def make_request(host, port, data, receive_something=False, timeout_seconds=None):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_seconds)

        sock.connect((host, port))
        sock.sendall(dill.dumps(data))

        if receive_something:
            received = dill.loads(sock.recv(1024))
            return received


def check_max_evaluations(base_result_directory, max_evaluations, networking_directory):
    logger.debug("Checking if max evaluations is reached")
    previous_results, _ = load_state(base_result_directory)
    max_evaluations_is_reached = (
        max_evaluations is not None and len(previous_results) >= max_evaluations
    )
    shutdown_file = networking_directory / "shutdown"
    if max_evaluations_is_reached:
        if not shutdown_file.exists():
            logger.debug("Max evaluations is reached, creating shutdown file")
            shutdown_file.touch()
        return True
    elif shutdown_file.exists():
        shutdown_file.unlink()
    return False


def nic_name_to_host(nic_name):
    """Helper function to translate the name of a network card into a valid host name"""
    try:
        # See https://pypi.org/project/netifaces/
        host = netifaces.ifaddresses(nic_name).setdefault(
            netifaces.AF_INET, [{"addr": "No IP addr"}]
        )
        host = host[0]["addr"]
    except ValueError:
        raise ValueError(
            f"You must specify a valid interface name. "
            f"Available interfaces are: {' '.join(netifaces.interfaces())}"
        )
    return host


class MasterLocker:
    def __init__(self, lock_path):
        atexit.register(self.__del__)
        self.master_lock_file = lock_path.open("a")  # a for security

    def __del__(self):
        self.master_lock_file.close()

    def release_lock(self):
        fcntl.lockf(self.master_lock_file, fcntl.LOCK_UN)

    def acquire_lock(self):
        try:
            fcntl.lockf(self.master_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def start_tcp_server(machine_host, timeout, handler):
    # https://stackoverflow.com/questions/22549044/why-is-port-not-immediately-released-after-the-socket-closes
    socketserver.TCPServer.allow_reuse_address = True  # Do we really want this?
    server = None
    while server is None:
        port = random.randint(10000, 13000)
        try:
            server = socketserver.TCPServer((machine_host, port), handler)
        except OSError:
            logger.debug(f"Socket {machine_host}:{port} already used, trying another one")
    server.timeout = timeout  # TODO recheck
    return server
