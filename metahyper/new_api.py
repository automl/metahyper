import logging
from pathlib import Path

from metahyper._communication_utils import (
    MasterLocker,
    check_max_evaluations,
    nic_name_to_host,
)
from metahyper._master import service_loop_master_activities
from metahyper._worker import service_loop_worker_activities, start_worker_server

logger = logging.getLogger(__name__)


def run(
    evaluation_fn,
    sampler,
    optimization_dir,
    master_handling_timeout=10,
    development_stage_id=None,
    task_id=None,
    network_interface=None,
    can_be_master=True,
    is_worker=True,
    max_evaluations=None,
):
    # TODO: allow alg. developer user to set logger name
    # Result read-out script / provide master sided live log / tensorboard
    if network_interface is not None:
        machine_host = nic_name_to_host(network_interface)
    else:
        machine_host = "127.0.0.1"  # Localhost

    optimization_dir = Path(optimization_dir)
    # TODO: give master the dev / task dirs
    if development_stage_id is not None:
        optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    if task_id is not None:
        optimization_dir = Path(optimization_dir) / f"task_{task_id}"

    base_result_directory = optimization_dir / "results"
    base_result_directory.mkdir(parents=True, exist_ok=True)

    networking_dir = optimization_dir / ".networking"
    networking_dir.mkdir(exist_ok=True)

    master_location_file = networking_dir / "master.location"
    master_location_file.touch(exist_ok=True)

    master_lock_file = networking_dir / "master.lock"
    master_lock_file.touch(exist_ok=True)
    master_locker = MasterLocker(master_lock_file)

    if max_evaluations is not None and check_max_evaluations(
        base_result_directory, max_evaluations, networking_dir
    ):
        logger.debug("Shutting down")
        exit(0)

    worker_server = None
    master_process = None
    evaluation_process = None
    try:
        if is_worker:
            worker_server = start_worker_server(machine_host)

        while True:
            if max_evaluations is not None and check_max_evaluations(
                base_result_directory, max_evaluations, networking_dir
            ):
                logger.debug("Shutting down")
                exit(0)

            if can_be_master:
                master_process, master_locker = service_loop_master_activities(
                    base_result_directory,
                    master_handling_timeout,
                    machine_host,
                    master_location_file,
                    master_locker,
                    master_process,
                    sampler,
                    networking_dir,
                    max_evaluations,
                )
            if is_worker:
                evaluation_process = service_loop_worker_activities(
                    evaluation_fn,
                    evaluation_process,
                    master_location_file,
                    worker_server,
                )
    finally:
        if is_worker and worker_server is not None:
            worker_server.server_close()
