import atexit
import fcntl
import logging
import multiprocessing
import socket
import socketserver
import time
from pathlib import Path

import dill
import netifaces


def nic_name_to_host(nic_name):
    """ Helper function to translate the name of a network card into a valid host name"""
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


class _MasterLocker:
    def __init__(self, lock_path):
        atexit.register(self.__del__)
        master_lock_file = Path(lock_path)
        master_lock_file.touch(exist_ok=True)
        self.master_lock_file = master_lock_file.open("a")  # a for security

    def __del__(self):
        self.master_lock_file.close()

    def acquire_lock(self):
        try:
            fcntl.lockf(self.master_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


class _MasterServerHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    sampler = None

    def handle(self):
        # TODO: proper handling
        data = dill.loads(self.request.recv(1024).strip())
        self.sampler.new_result(data)
        print("{} wrote:".format(self.client_address[0]))
        print(data)
        config = self.sampler.get_config()
        self.request.sendall(dill.dumps(config))


def _make_request(host, port, data, receive_something=False):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.connect((host, port))
        except ConnectionRefusedError:
            print("something bad happened")  # TODO: handle
        sock.sendall(dill.dumps(data))
        print("Sent:     {}".format(data))

        if receive_something:
            received = dill.loads(sock.recv(1024))  # TODO: error handling
            print("Received: {}".format(received))
            return received


def _start_master_server(host, port, sampler, master_lock_file, timeout=10):
    _MasterServerHandler.sampler = sampler  # TODO: explain necessity for the dirty
    # https://stackoverflow.com/questions/22549044/why-is-port-not-immediately-released-after-the-socket-closes
    socketserver.TCPServer.allow_reuse_address = True  # Do we really want this?
    with socketserver.TCPServer((host, port), _MasterServerHandler) as server:
        server.timeout = timeout
        with open(master_lock_file, "w") as master_lock_file_stream:
            master_lock_file_stream.write(f"{host}:{port}\n")
        try:
            while True:
                # TODO: worker bookkeeping
                server.handle_request()
        finally:
            server.shutdown()


def service_loop(host, port, evaluation_fn, sampler, master_handling_timeout=10):
    # TODO: add host port scan

    # TODO proper logging handling
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    master_lock_file = "master.lock"
    master_locker = _MasterLocker(master_lock_file)
    master_process = None
    evaluation_process = None
    while True:
        # Master activities
        if master_process is not None and not master_process.is_alive():
            logger.info("TODO release lock?")
        elif master_process is None and master_locker.acquire_lock():
            time.sleep(2)
            logger.info("Starting master server")
            master_process = multiprocessing.Process(
                name="master_server",
                target=_start_master_server,
                kwargs=dict(
                    host=host,
                    port=port,
                    sampler=sampler,
                    master_lock_file=master_lock_file,
                    timeout=master_handling_timeout,
                ),
                daemon=True,
            )
            master_process.start()

        time.sleep(2)

        # Worker activities
        if evaluation_process is None or not evaluation_process.is_alive():
            # TODO: read out result
            # TODO: Serialize result to disk

            # TODO: request symbols
            # TODO: read out master location from disk
            # TODO: error handling in case master is dead
            logger.info("Requesting new config")
            new_config = _make_request(
                host, port, "give_me_new_config", receive_something=True
            )

            logger.info(f"Starting up new evaluation process with config {new_config}")
            evaluation_process = multiprocessing.Process(
                name="evaluation_process",
                target=evaluation_fn,
                kwargs=dict(
                    config=new_config,
                    config_working_directory=".",
                    previous_working_directory=None,
                ),
                daemon=True,
            )
            evaluation_process.start()
        elif True:  # TODO condition
            logger.info("Letting master know I am still alive")
            _make_request(host, port, "am_alive", receive_something=False)

        time.sleep(5)


if __name__ == "__main__":

    class Sampler:
        def __init__(self, config_space):
            self.config_space = config_space
            self.results = []

        def new_result(self, result):
            self.results.append(result)

        def get_config(self):
            return len(self.results)

    def evaluation_fn(config, config_working_directory, previous_working_directory):
        time.sleep(20)
        return "evald"

    service_loop("localhost", 9999, evaluation_fn, Sampler(dict()))
