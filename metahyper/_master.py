import logging
import multiprocessing
import random
import socket
import socketserver
import time

import dill

from metahyper._networking_utils import make_request
from metahyper.status import load_state

logger = logging.getLogger(__name__)


# TODO: move
def check_max_evaluations(base_result_directory, max_evaluations, networking_directory):
    logger.info("Checking if max evaluations is reached")
    previous_results, _ = load_state(base_result_directory)
    max_evaluations_is_reached = len(previous_results) >= max_evaluations
    shutdown_file = networking_directory / "shutdown"
    if max_evaluations_is_reached:
        if not shutdown_file.exists():
            logger.info("Max evaluations is reached, creating shutdown file")
            shutdown_file.touch()
        logger.info("Shutting down")
        exit(0)
    elif shutdown_file.exists():
        shutdown_file.unlink()


def _try_to_read_worker_result(base_result_directory, worker_file):
    if worker_file.exists():  # Worker already finished an evaluation
        config_id = worker_file.read_text()
        config_result_file = base_result_directory / f"config_{config_id}" / "result.dill"
        if config_result_file.exists():
            with config_result_file.open("rb") as config_result_stream:
                return dill.load(config_result_stream), config_id
        else:
            return None, config_id
    else:
        return None, None


class _MasterServerHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    sampler = None
    base_result_directory = None
    networking_directory = None

    def handle(self):
        data = dill.loads(self.request.recv(1024).strip())
        request_id, worker_host, worker_port = data
        logger.info(f"{worker_host}:{worker_port} wrote: {request_id}")

        # Check if result needs to be read
        worker_file = self.networking_directory / f"worker_{worker_host}:{worker_port}"
        worker_result, config_id = _try_to_read_worker_result(
            self.base_result_directory, worker_file
        )
        if worker_result is not None:
            self.sampler.new_result(worker_result, config_id)
            logger.info(f"Added result for config {config_id}")

        # TODO: handle Connected to master but did not receive an answer.
        config, config_id = self.sampler.get_config_and_id()

        # Worker Bookkeeping
        config_working_directory = self.base_result_directory / f"config_{config_id}"
        config_working_directory.mkdir()
        config_file = config_working_directory / "config.dill"
        with config_file.open("wb") as config_file_stream:
            # TODO: allow alg developer to allow json logging
            dill.dump(config, config_file_stream)
        worker_file.touch(exist_ok=True)
        worker_file.write_text(config_id)  # TODO: handle crash before this line

        # Request answering
        request_answer = dict(
            config_id=config_id,
            config=config,
            config_working_directory=config_working_directory,
            previous_working_directory=None,
        )
        self.request.sendall(dill.dumps(request_answer))


def _start_master_server(
    host,
    sampler,
    master_location_file,
    base_result_directory,
    networking_directory,
    max_evaluations,
    timeout=10,
):
    check_max_evaluations(base_result_directory, max_evaluations, networking_directory)

    port = random.randint(8000, 9999)  # TODO: add host port scan

    # The handler gets instantiated on each request, so, to have persistent parts we use
    # class attributes.
    _MasterServerHandler.sampler = sampler
    _MasterServerHandler.base_result_directory = base_result_directory
    _MasterServerHandler.networking_directory = networking_directory

    logger.info("Reading in previous results")
    previous_results, pending_configs = load_state(base_result_directory)

    logger.info(
        f"Read in previous_results={previous_results}, pending_configs={pending_configs}"
    )
    sampler.load_results(previous_results, pending_configs)

    # TODO!: previous working dir functionality

    # https://stackoverflow.com/questions/22549044/why-is-port-not-immediately-released-after-the-socket-closes
    socketserver.TCPServer.allow_reuse_address = True  # Do we really want this?
    # TODO: manual try finally to make sure lock file gets released asap
    with socketserver.TCPServer((host, port), _MasterServerHandler) as server:
        server.timeout = timeout  # TODO recheck
        master_location_file.write_text(f"{host}:{port}")
        while True:
            if max_evaluations is not None:
                check_max_evaluations(
                    base_result_directory, max_evaluations, networking_directory
                )

            # TODO: do not check for aliveness every iteration
            # TODO: investigate connection reset error further (kill worker during req)
            for worker_file in networking_directory.glob("worker_*"):
                worker_host, worker_port = worker_file.name[len("worker_") :].split(":")
                worker_port = int(worker_port)
                logger.info(f"Checking if worker {worker_host}:{worker_port} is alive")

                try:
                    make_request(
                        worker_host,
                        worker_port,
                        "alive?",
                        receive_something=True,
                        timeout_seconds=timeout,
                    )
                except (ConnectionRefusedError, ConnectionResetError):
                    logger.info("Worker not alive")
                    worker_file = (
                        networking_directory / f"worker_{worker_host}:{worker_port}"
                    )
                    worker_result, config_id = _try_to_read_worker_result(
                        base_result_directory, worker_file
                    )
                    if worker_result is None:
                        dead_flag = (
                            base_result_directory / f"config_{config_id}" / "dead.flag"
                        )
                        dead_flag.touch()
                        worker_file.unlink()
                        logger.info("Added dead flag and removed worker")
                    else:
                        sampler.new_result(worker_result, config_id)
                        logger.info("Added result from dead worker.")
                except EOFError:
                    # TODO: Handle
                    logger.info(
                        "Connected to worker but did not receive an answer. Did the worker die?"
                    )
                except socket.timeout:
                    # TODO: Handle
                    logger.info(
                        "Connected to worker but its not answering request, perhaps it "
                        "awaits a new config."
                    )

            server.handle_request()


def service_loop_master_activities(
    base_result_directory,
    master_handling_timeout,
    master_host,
    master_location_file,
    master_locker,
    master_process,
    sampler,
    networking_directory,
    max_evaluations,
):
    if master_process is not None and not master_process.is_alive():
        master_process = None
        master_locker.release_lock()
        logger.info("Master process died, releasing master lock.")
    elif master_process is None and master_locker.acquire_lock():
        time.sleep(2)
        logger.info("Starting master server")
        master_process = multiprocessing.Process(
            name="master_server",
            target=_start_master_server,
            kwargs=dict(
                host=master_host,
                sampler=sampler,
                master_location_file=master_location_file,
                base_result_directory=base_result_directory,
                networking_directory=networking_directory,
                max_evaluations=max_evaluations,
                timeout=master_handling_timeout,
            ),
            daemon=True,
        )
        master_process.start()

    return master_process, master_locker  # Return avoids master locker not working
