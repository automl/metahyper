import logging
import time
from pathlib import Path

import dill
import more_itertools

from metahyper._utils import Locker
from metahyper.status import load_state

logger = logging.getLogger(__name__)


def _check_max_evaluations(base_result_directory, max_evaluations):
    logger.debug("Checking if max evaluations is reached")
    previous_results, *_ = load_state(base_result_directory)
    max_evaluations_is_reached = (
        max_evaluations is not None and len(previous_results) >= max_evaluations
    )
    if max_evaluations_is_reached:
        logger.debug("Max evaluations is reached, creating shutdown file")
        return True
    return False


def _sample_config(base_result_directory, sampler):
    logger.debug("Reading in previous results")
    previous_results, pending_configs, pending_configs_free = load_state(
        base_result_directory
    )
    logger.debug(
        f"Read in previous_results={previous_results}, "
        f"pending_configs={pending_configs}, "
        f"pending_configs_free={pending_configs_free}, "
    )

    if pending_configs_free:
        config_id, config = more_itertools.first(pending_configs_free.items())
        config_working_directory = base_result_directory / f"config_{config_id}"
        previous_config_id_file = config_working_directory / "previous_config.id"
        if previous_config_id_file.exists():
            previous_config_id = previous_config_id_file.read_text()
        else:
            previous_config_id = None
    else:
        sampler.load_results(previous_results, pending_configs)
        logger.info(
            f"Loaded {len(previous_results)} finished evaluations and "
            f"{len(pending_configs)} pending evaluations"
        )

        config, config_id, previous_config_id = sampler.get_config_and_ids()

        config_working_directory = base_result_directory / f"config_{config_id}"
        config_working_directory.mkdir(exist_ok=True)
        if previous_config_id is not None:
            previous_config_id_file = config_working_directory / "previous_config.id"
            previous_config_id_file.write_text(previous_config_id)

    if previous_config_id is not None:
        previous_working_directory = Path(
            base_result_directory, f"config_{previous_config_id}"
        )
    else:
        previous_working_directory = None

    # We want this to be the last action in sampling to catch potential crashes
    with Path(config_working_directory, "config.dill").open("wb") as config_stream:
        dill.dump(config, config_stream)

    return config, config_working_directory, previous_working_directory


def run(
    evaluation_fn,
    sampler,
    optimization_dir,
    development_stage_id=None,
    task_id=None,
    max_evaluations=None,
):
    optimization_dir = Path(optimization_dir)
    if development_stage_id is not None:
        optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    if task_id is not None:
        optimization_dir = Path(optimization_dir) / f"task_{task_id}"

    base_result_directory = optimization_dir / "results"
    base_result_directory.mkdir(parents=True, exist_ok=True)

    decision_lock_file = optimization_dir / ".decision_lock"
    decision_lock_file.touch(exist_ok=True)
    decision_locker = Locker(decision_lock_file)

    while True:
        if max_evaluations is not None and _check_max_evaluations(
            base_result_directory, max_evaluations
        ):
            logger.debug("Shutting down")
            exit(0)

        if decision_locker.acquire_lock():
            config, config_working_directory, previous_working_directory = _sample_config(
                base_result_directory, sampler
            )

            config_lock_file = config_working_directory / ".config_lock"
            config_lock_file.touch(exist_ok=True)
            config_locker = Locker(config_lock_file)
            config_lock_acquired = config_locker.acquire_lock()
            decision_locker.release_lock()
            if config_lock_acquired:
                try:
                    result = evaluation_fn(
                        config=config,
                        config_working_directory=config_working_directory,
                        previous_working_directory=previous_working_directory,
                    )
                except Exception:
                    logger.error("Evaluation process crashed")
                    result = "error"
                with Path(config_working_directory, "result.dill").open(
                    "wb"
                ) as result_open:
                    dill.dump(result, result_open)
                logger.info("Finished evaluating a config")
                config_locker.release_lock()
        else:
            time.sleep(5)
