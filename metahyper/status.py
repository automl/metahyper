import logging
import pathlib
import pprint

import dill

from metahyper._utils import Locker

logger = logging.getLogger(__name__)


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
            config_dir.rmdir()  # Worker crashed

    return previous_results, pending_configs, pending_configs_free


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=pathlib.Path)
    parser.add_argument("--configs", action="store_true")
    args = parser.parse_args()

    previous_results, pending_configs, pending_configs_free = load_state(
        args.run_directory / "results"
    )
    print(f"#Evaluated configs: {len(previous_results)}")
    print(f"#Pending configs: {len(pending_configs)}")
    print(f"#Pending configs without worker: {len(pending_configs_free)}")
    if args.configs:
        print()
        print("Evaluated configs:")
        pprint.pprint(previous_results)
