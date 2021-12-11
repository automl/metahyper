import logging
import multiprocessing
import pprint
import socketserver

import dill

from metahyper._communication_utils import make_request, start_tcp_server

logger = logging.getLogger(__name__)


def _serialize_result(evaluation_fn, location, *eval_args, **eval_kwargs):
    # TODO: allow alg developer to allow json logging
    result = evaluation_fn(*eval_args, **eval_kwargs)
    with location.open("wb") as location_stream:
        dill.dump(result, location_stream)


def _read_master_address(master_location_file):
    master_host, master_port = master_location_file.read_text().split(":")
    master_port = int(master_port)
    logger.debug(f"Worker using master_host={master_host} and port={master_port}")
    return master_host, master_port


class _WorkerServerHandler(socketserver.BaseRequestHandler):
    """
    The request handler class for our server.

    It is instantiated once per connection to the server, and must
    override the handle() method to implement communication to the
    client.
    """

    def handle(self):
        data = dill.loads(self.request.recv(1024).strip())
        logger.debug(f"Master wrote: {data}")
        self.request.sendall(dill.dumps(dict()))


def start_worker_server(machine_host, timeout=5):
    return start_tcp_server(machine_host, timeout, _WorkerServerHandler)


def service_loop_worker_activities(
    evaluation_fn,
    evaluation_process,
    master_location_file,
    worker_server,
    timeout_seconds,
):
    worker_server.handle_request()

    evaluation_process_crashed = (
        evaluation_process is not None
        and not evaluation_process.is_alive()
        and evaluation_process.exitcode != 0
    )
    if evaluation_process_crashed:
        logger.error("Evaluation process crashed")
        result_file = (
            evaluation_process.evaluation_spec["config_working_directory"] / "result.dill"
        )
        with result_file.open("wb") as location_stream:
            dill.dump("error", location_stream)

    if evaluation_process is None or not evaluation_process.is_alive():
        try:
            master_host, master_port = _read_master_address(master_location_file)
            worker_host, worker_port = worker_server.server_address

            logger.info(f"Worker {worker_host}:{worker_port} requesting new config")
            request = ["give_me_new_config"] + list(worker_server.server_address)
            evaluation_spec = make_request(
                master_host,
                master_port,
                request,
                receive_something=True,
                timeout_seconds=timeout_seconds,
            )

            logger.info(
                "Starting up new evaluation process with config "
                f"{pprint.pformat(evaluation_spec)}"
            )
            evaluation_process = multiprocessing.Process(
                name="evaluation_process",
                target=_serialize_result,
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
            evaluation_process.evaluation_spec = evaluation_spec
        except ConnectionRefusedError:
            logger.warning("Could not connect to master server.")
        except ConnectionResetError:
            logger.warning("Connection was reset from master")
        except EOFError:
            logger.warning(
                "Connected to master but did not receive an answer. Did the master die?"
            )

    return evaluation_process