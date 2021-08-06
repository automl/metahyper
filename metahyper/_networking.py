import atexit
import fcntl
import logging
import multiprocessing
import socket
import socketserver
import time
import uuid
from pathlib import Path

import dill
import netifaces

logger = logging.getLogger(__name__)


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
        self.master_lock_file = lock_path.open("a")  # a for security

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

    # TODO: read in results from disk
    # TODO: load results from disk when master is restartet
    # TODO: request symbols (enum)
    sampler = None
    base_result_directory = None

    def handle(self):
        # TODO: verify master always having the up to date configs
        data = dill.loads(self.request.recv(1024).strip())
        logger.info(f"{self.client_address[0]} wrote: {data}")

        if data == "am_alive":
            # TODO: worker bookkeeping
            pass
        elif data == "give_me_new_config":
            self.sampler.new_result(data)
            config, config_id = self.sampler.get_config_and_id()
            config_working_directory = self.base_result_directory / f"config_{config_id}"
            config_working_directory.mkdir()
            # TODO: document that config_id is used for results path

            request_answer = dict(
                config_id=config_id,
                config=config,
                config_working_directory=config_working_directory,
                previous_working_directory=None,
            )
            self.request.sendall(dill.dumps(request_answer))
        else:
            raise ValueError(f"Invalid request from worker: {data}")


def _make_request(host, port, data, receive_something=False):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.connect((host, port))
        except ConnectionRefusedError:
            logger.warning("something bad happened")  # TODO: handle
        sock.sendall(dill.dumps(data))
        logger.info("Worker sent:     {}".format(data))

        if receive_something:
            received = dill.loads(sock.recv(1024))  # TODO: error handling
            logger.info("Worker received: {}".format(received))
            return received


def _start_master_server(
    host, port, sampler, master_lock_file, base_result_directory, timeout=10
):
    # TODO: add host port scan

    # The handler gets instantiated on each request, so, to have persistent parts we use
    # class attributes.
    _MasterServerHandler.sampler = sampler
    _MasterServerHandler.base_result_directory = base_result_directory

    # https://stackoverflow.com/questions/22549044/why-is-port-not-immediately-released-after-the-socket-closes
    socketserver.TCPServer.allow_reuse_address = True  # Do we really want this?
    with socketserver.TCPServer((host, port), _MasterServerHandler) as server:
        server.timeout = timeout
        with master_lock_file.open("w") as master_lock_file_stream:
            master_lock_file_stream.write(f"{host}:{port}\n")
        try:
            while True:
                server.handle_request()
        finally:
            server.shutdown()


def service_loop(
    host,
    port,
    evaluation_fn,
    sampler,
    optimization_dir,
    master_handling_timeout=10,
    development_stage_id=None,
    task_id=None,
):
    # TODO: network_interface api
    # TODO: previous working dir functionality
    # TODO proper logging handling

    # TODO: add task / adj support
    optimization_dir = Path(optimization_dir)
    if development_stage_id is not None:
        optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    if task_id is not None:
        optimization_dir = Path(optimization_dir) / f"task_{task_id}"

    base_result_directory = optimization_dir / "results"
    base_result_directory.mkdir(parents=True, exist_ok=True)

    master_lock_file = optimization_dir / ".networking" / "master.lock"
    master_lock_file.parent.mkdir(exist_ok=True)
    master_lock_file.touch(exist_ok=True)
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
                    base_result_directory=base_result_directory,
                    timeout=master_handling_timeout,
                ),
                daemon=True,
            )
            master_process.start()

        time.sleep(2)

        def serialize_result(evaluation_fn, location, *eval_args, **eval_kwargs):
            print("location", location)
            print("args", eval_args)
            print("kwargs", eval_kwargs)
            result = evaluation_fn(*eval_args, **eval_kwargs)
            with location.open("wb") as location_stream:
                dill.dump(result, location_stream)

        # Worker activities
        if evaluation_process is None or not evaluation_process.is_alive():
            # TODO: read out master location from disk
            # TODO: error handling in case master read from disk is dead
            logger.info("Requesting new config")
            evaluation_spec = _make_request(
                host, port, "give_me_new_config", receive_something=True
            )

            # TODO: pretty print
            logger.info(f"Starting up new evaluation process with {evaluation_spec}")
            evaluation_process = multiprocessing.Process(
                name="evaluation_process",
                target=serialize_result,
                kwargs=dict(
                    evaluation_fn=evaluation_fn,
                    location=evaluation_spec["config_working_directory"] / "result.dill",
                    config=evaluation_spec["config"],
                    config_working_directory=evaluation_spec["config_working_directory"],
                    previous_working_directory=evaluation_spec[
                        "previous_working_directory"
                    ],
                ),
                daemon=True,
            )
            evaluation_process.start()
        elif True:  # TODO condition
            logger.info("Letting master know I am still alive")
            _make_request(host, port, "am_alive", receive_something=False)

        time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    class Sampler:
        def __init__(self, config_space):
            self.config_space = config_space
            self.results = []

        def new_result(self, result):
            self.results.append(result)

        def get_config_and_id(self):
            config_id = str(uuid.uuid4())[:6]
            return dict(a=len(self.results)), config_id

    def evaluation_fn(config, config_working_directory, previous_working_directory):
        time.sleep(20)
        return "evald"

    import shutil

    try:
        shutil.rmtree("test_opt_dir")
    except:
        pass
    service_loop(
        "localhost", 9999, evaluation_fn, Sampler(dict()), optimization_dir="test_opt_dir"
    )
