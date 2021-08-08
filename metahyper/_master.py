import logging
import multiprocessing
import random
import socketserver
import time

import dill

logger = logging.getLogger(__name__)


class _MasterServerHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    sampler = None
    base_result_directory = None

    def handle(self):
        data = dill.loads(self.request.recv(1024).strip())
        logger.info(f"{self.client_address[0]} wrote: {data}")  # TODO: port?

        if data == "am_alive":
            # TODO!: worker bookkeeping, if worker died, check if it did write to disk
            # TODO!: previous working dir functionality
            # TODO!: worker: send config_id its worker on
            # TODO!: flag config dir of dead worker as being dead
            pass
        elif data == "give_me_new_config":
            # TODO: handle Connected to master but did not receive an answer.
            # TODO!: read in result from disk, give config_id here, maybe save with result
            fake_config_id = random.randint(0, 1000)
            self.sampler.new_result(data, fake_config_id)
            config, config_id = self.sampler.get_config_and_id()
            config_working_directory = self.base_result_directory / f"config_{config_id}"
            config_working_directory.mkdir()

            # TODO: allow alg developer to allow json logging
            config_file = config_working_directory / "config.dill"
            with config_file.open("wb") as config_file_stream:
                dill.dump(config, config_file_stream)

            request_answer = dict(
                config_id=config_id,
                config=config,
                config_working_directory=config_working_directory,
                previous_working_directory=None,
            )
            self.request.sendall(dill.dumps(request_answer))
        else:
            raise ValueError(f"Invalid request from worker: {data}")


def _load_previous_state(base_result_directory):
    previous_results = dict()
    pending_configs = dict()
    for config_dir in base_result_directory.iterdir():
        result_file = config_dir / "result.dill"
        worker_dead_file = config_dir / "worker.dead"
        config_file = config_dir / "config.dill"
        config_id = config_dir.name[len("config_") :]

        if result_file.exists():
            with result_file.open("rb") as results_file_stream:
                previous_results[config_id] = dill.load(results_file_stream)
        elif worker_dead_file.exists():
            pass  # TODO: handle master crashed before letting config restart
        else:
            # TODO: handle master crashed before config file created
            with config_file.open("rb") as config_file_stream:
                pending_configs[config_id] = dill.load(config_file_stream)

    return previous_results, pending_configs


def _start_master_server(
    host, sampler, master_location_file, base_result_directory, timeout=10
):
    port = 9999  # TODO!: add host port scan

    # The handler gets instantiated on each request, so, to have persistent parts we use
    # class attributes.
    _MasterServerHandler.sampler = sampler
    _MasterServerHandler.base_result_directory = base_result_directory

    logger.info("Reading in previous results")
    previous_results, pending_configs = _load_previous_state(base_result_directory)

    logger.info(
        f"Read in previous_results={previous_results}, pending_configs={pending_configs}"
    )
    sampler.load_results(previous_results, pending_configs)

    # https://stackoverflow.com/questions/22549044/why-is-port-not-immediately-released-after-the-socket-closes
    socketserver.TCPServer.allow_reuse_address = True  # Do we really want this?
    with socketserver.TCPServer((host, port), _MasterServerHandler) as server:
        server.timeout = timeout
        with master_location_file.open("w") as master_lock_file_stream:
            master_lock_file_stream.write(f"{host}:{port}\n")
        try:
            while True:
                server.handle_request()
        finally:
            server.shutdown()


def service_loop_master_activities(
    base_result_directory,
    master_handling_timeout,
    master_host,
    master_location_file,
    master_locker,
    master_process,
    sampler,
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
                timeout=master_handling_timeout,
            ),
            daemon=True,
        )
        master_process.start()

    return master_process, master_locker  # Return avoids master locker not working
