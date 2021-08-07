import logging
import multiprocessing
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

    # TODO: previous working dir functionality
    # TODO: read in results from disk
    # TODO: load results from disk when master is restartet
    # TODO: request symbols (enum)
    sampler = None
    base_result_directory = None

    def handle(self):
        # TODO: verify master always having the up to date configs
        data = dill.loads(self.request.recv(1024).strip())
        logger.info(f"{self.client_address[0]} wrote: {data}")  # TODO: port?

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


def _start_master_server(
    host, sampler, master_location_file, base_result_directory, timeout=10
):
    port = 9999  # TODO: add host port scan

    # The handler gets instantiated on each request, so, to have persistent parts we use
    # class attributes.
    _MasterServerHandler.sampler = sampler
    _MasterServerHandler.base_result_directory = base_result_directory

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
