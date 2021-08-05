import atexit
import fcntl
import socket
import socketserver
from pathlib import Path

import dill
import netifaces


def nic_name_to_host(nic_name):
    """ Helper function to translate the name of a network card into a valid host name"""
    try:
        # See https://pypi.org/project/netifaces/
        host = netifaces.ifaddresses(nic_name).setdefault(
            netifaces.AF_INET, [{"addr": "No IP addr"}]
        )[0]["addr"]
    except ValueError:
        raise ValueError(
            f"You must specify a valid interface name. "
            f"Available interfaces are: {' '.join(netifaces.interfaces())}"
        )
    return host


class Sampler:
    def __init__(self, config_space):
        self.config_space = config_space
        self.results = []

    def new_result(self, result):
        self.results.append(result)

    def get_config(self):
        return len(self.results)


class MasterServerHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    sampler = None

    def handle(self):
        self.data = data = self.request.recv(1024).strip()
        self.sampler.new_result(data)
        print("{} wrote:".format(self.client_address[0]))
        print(self.data)
        config = self.sampler.get_config()
        self.request.sendall(dill.dumps(config))


def start_master_server():
    HOST, PORT = "localhost", 9999
    MasterServerHandler.sampler = Sampler(dict())  # TODO: explain necessity for the dirty
    with socketserver.TCPServer((HOST, PORT), MasterServerHandler) as server:
        server.serve_forever()


def start_worker_server():
    HOST, PORT = "localhost", 9999
    data = "a"

    # Create a socket (SOCK_STREAM means a TCP socket)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Connect to server and send data
        sock.connect((HOST, PORT))
        sock.sendall(bytes(data + "\n", "utf-8"))

        # Receive data from the server and shut down
        received = dill.loads(sock.recv(1024))

    print("Sent:     {}".format(data))
    print("Received: {}".format(received))


class MasterLocker:
    def __init__(self, lock_path):
        atexit.register(self.__del__)
        master_lock_file = Path(lock_path)
        master_lock_file.touch(exist_ok=True)
        self.master_lock_file = master_lock_file.open("a")  # Use "a" for security reasons

    def __del__(self):
        self.master_lock_file.close()

    def acquire_lock(self):
        try:
            fcntl.lockf(self.master_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


if __name__ == "__main__":
    master_locker = MasterLocker(".lock_master")
    if master_locker.acquire_lock():
        start_master_server()
    else:
        start_worker_server()
