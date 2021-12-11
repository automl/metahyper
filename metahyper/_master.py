import logging
import multiprocessing
import socket
import socketserver
import time

import dill

from metahyper._communication_utils import (
    check_max_evaluations,
    make_request,
    start_tcp_server,
)
from metahyper.status import load_state

logger = logging.getLogger(__name__)


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
        logger.info(
            f"Master received request {request_id} from worker {worker_host}:{worker_port}"
        )

        # Check if result needs to be read
        worker_file = self.networking_directory / f"worker_{worker_host}:{worker_port}"
        worker_result, config_id = _try_to_read_worker_result(
            self.base_result_directory, worker_file
        )
        if worker_result is not None:
            self.sampler.new_result(worker_result, config_id)
            logger.info(f"Added result for config {config_id}")

        # TODO: handle Connected to master but did not receive an answer.
        config, config_id, previous_config_id = self.sampler.get_config_and_ids()

        # Worker Bookkeeping
        # TODO: handle crashes for the next code blocks
        config_working_directory = self.base_result_directory / f"config_{config_id}"
        config_working_directory.mkdir()

        if previous_config_id is not None:
            previous_config_id_file = config_working_directory / "previous_config_id.txt"
            previous_config_id_file.write_text(previous_config_id)
            previous_working_directory = (
                self.base_result_directory / f"config_{previous_config_id}"
            )
        else:
            previous_working_directory = None

        config_file = config_working_directory / "config.dill"
        with config_file.open("wb") as config_file_stream:
            # TODO: allow alg developer to allow json logging
            dill.dump(config, config_file_stream)

        worker_file.touch(exist_ok=True)
        worker_file.write_text(config_id)

        # Request answering
        request_answer = dict(
            config_id=config_id,
            config=config,
            config_working_directory=config_working_directory,
            previous_working_directory=previous_working_directory,
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
    if check_max_evaluations(
        base_result_directory, max_evaluations, networking_directory
    ):
        logger.debug("Shutting down")
        exit(0)

    # The handler gets instantiated on each request, so, to have persistent parts we use
    # class attributes.
    _MasterServerHandler.sampler = sampler
    _MasterServerHandler.base_result_directory = base_result_directory
    _MasterServerHandler.networking_directory = networking_directory

    logger.debug("Reading in previous results")
    previous_results, pending_configs = load_state(base_result_directory)
    logger.debug(
        f"Read in previous_results={previous_results}, pending_configs={pending_configs}"
    )

    sampler.load_results(previous_results, pending_configs)
    logger.info(
        f"Loaded {len(previous_results)} finished evaluations and "
        f"{len(pending_configs)} pending evaluations"
    )

    server = start_tcp_server(host, timeout, _MasterServerHandler)
    try:
        _, port = server.server_address
        master_location_file.write_text(f"{host}:{port}")
        while True:
            if max_evaluations is not None and check_max_evaluations(
                base_result_directory, max_evaluations, networking_directory
            ):
                logger.debug("Shutting down")
                exit(0)

            # TODO: do not check for aliveness every iteration
            # TODO: investigate connection reset error further (kill worker during req)
            for worker_file in networking_directory.glob("worker_*"):
                worker_host, worker_port = worker_file.name[len("worker_") :].split(":")
                worker_port = int(worker_port)
                logger.debug(f"Checking if worker {worker_host}:{worker_port} is alive")

                try:
                    make_request(
                        worker_host,
                        worker_port,
                        "alive?",
                        receive_something=True,
                        timeout_seconds=timeout,
                    )
                except (ConnectionRefusedError, ConnectionResetError):
                    logger.info(f"Worker {worker_host}:{worker_port} died")
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
                        # !!TODO: handle
                        logger.debug("Added dead flag and removed worker")
                    else:
                        sampler.new_result(worker_result, config_id)
                        logger.debug("Added result from dead worker.")
                except EOFError:
                    # TODO: Handle
                    logger.debug(
                        "Connected to worker but did not receive an answer. Did the worker die?"
                    )
                except socket.timeout:
                    # TODO: Handle
                    logger.debug(
                        "Connected to worker but its not answering request, perhaps it "
                        "awaits a new config."
                    )

            server.handle_request()
    finally:
        server.server_close()


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