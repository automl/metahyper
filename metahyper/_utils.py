import atexit
import fcntl
import logging

import dill

logger = logging.getLogger(__name__)


class Locker:
    def __init__(self, lock_path):
        atexit.register(self.__del__)
        self.lock_path = lock_path
        self.lock_file = self.lock_path.open("a")  # a for security

    def __del__(self):
        self.lock_file.close()

    def release_lock(self):
        logger.debug(f"Release lock for {self.lock_path}")
        fcntl.lockf(self.lock_file, fcntl.LOCK_UN)

    def acquire_lock(self):
        try:
            logger.debug(f"Acquired lock for {self.lock_path}")
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            logger.debug(f"Failed to acquire lock for {self.lock_path}")
            return False


def load_state(base_result_directory):
    logger.debug(f"Loading state from {base_result_directory}")
    previous_results = dict()
    pending_configs = dict()
    pending_configs_free = dict()
    for config_dir in base_result_directory.iterdir():
        config_id = config_dir.name[len("config_") :]
        result_file = config_dir / "result.dill"
        config_file = config_dir / "config.dill"
        if result_file.exists():
            with result_file.open("rb") as results_file_stream:
                previous_results[config_id] = dill.load(results_file_stream)
        elif config_file.exists():
            with config_file.open("rb") as config_file_stream:
                pending_configs[config_id] = dill.load(config_file_stream)

            config_lock_file = config_dir / ".config_lock"
            config_locker = Locker(config_lock_file)
            if config_locker.acquire_lock():
                pending_configs_free[config_id] = pending_configs[config_id]
        else:
            logger.info(f"Removing {config_dir} as worker died during config sampling.")
            config_dir.rmdir()  # Worker crashed

    logger.debug(
        f"Read in previous_results={previous_results}, "
        f"pending_configs={pending_configs}, "
        f"and pending_configs_free={pending_configs_free}, "
    )
    return previous_results, pending_configs, pending_configs_free
