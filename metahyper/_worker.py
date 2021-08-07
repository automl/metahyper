import logging
import multiprocessing
import pprint
import socket

import dill

logger = logging.getLogger(__name__)


def _make_request(host, port, data, receive_something=False):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, port))
        sock.sendall(dill.dumps(data))

        if receive_something:
            received = dill.loads(sock.recv(1024))
            return received


def service_loop_worker_activities(
    evaluation_fn, evaluation_process, master_location_file
):
    def serialize_result(evaluation_fn, location, *eval_args, **eval_kwargs):
        result = evaluation_fn(*eval_args, **eval_kwargs)
        with location.open("wb") as location_stream:
            dill.dump(result, location_stream)

    def read_master_address(master_location_file):
        master_host, master_port = master_location_file.read_text().split(":")
        master_port = int(master_port)
        logger.debug(f"Worker using master_host={master_host} and port={master_port}")
        return master_host, master_port

    try:
        if evaluation_process is None or not evaluation_process.is_alive():
            master_host, master_port = read_master_address(master_location_file)

            logger.info("Worker requesting new config")
            evaluation_spec = _make_request(
                master_host, master_port, "give_me_new_config", receive_something=True
            )

            logger.info(
                f"Starting up new evaluation process with {pprint.pformat(evaluation_spec)}"
            )
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
            master_host, master_port = read_master_address(master_location_file)
            logger.info("Letting master know I am still alive")
            _make_request(master_host, master_port, "am_alive", receive_something=False)
    except ConnectionRefusedError:
        logging.info("Could not connect to master server.")
    except EOFError:
        logging.info(
            "Connected to master but did not receive an answer. Did the master die?"
        )
        # TODO: handle on new master

    return evaluation_process
