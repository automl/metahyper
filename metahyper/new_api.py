import atexit
import fcntl
import logging
import time
from pathlib import Path

import netifaces

from metahyper._master import service_loop_master_activities
from metahyper._worker import service_loop_worker_activities

logger = logging.getLogger(__name__)


def _nic_name_to_host(nic_name):
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

    def release_lock(self):
        fcntl.lockf(self.master_lock_file, fcntl.LOCK_UN)

    def acquire_lock(self):
        try:
            fcntl.lockf(self.master_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def run(
    evaluation_fn,
    sampler,
    optimization_dir,
    master_handling_timeout=10,
    development_stage_id=None,
    task_id=None,
    network_interface=None,
):
    if network_interface is not None:
        master_host = _nic_name_to_host(network_interface)
    else:
        master_host = "localhost"

    optimization_dir = Path(optimization_dir)
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
    master_locker = _MasterLocker(master_lock_file)

    master_process = None
    evaluation_process = None
    while True:
        master_process, master_locker = service_loop_master_activities(
            base_result_directory,
            master_handling_timeout,
            master_host,
            master_location_file,
            master_locker,
            master_process,
            sampler,
        )
        time.sleep(2)

        service_loop_worker_activities(
            evaluation_fn, evaluation_process, master_location_file
        )
        time.sleep(2)
