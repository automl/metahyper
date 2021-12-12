import logging
import time
from pathlib import Path

import dill
import more_itertools

from metahyper._utils import Locker, load_state

logger = logging.getLogger("metahyper")


def _check_max_evaluations(base_result_directory, max_evaluations):
    logger.debug("Checking if max evaluations is reached")
    previous_results, *_ = load_state(base_result_directory)
    max_evaluations_is_reached = (
        max_evaluations is not None and len(previous_results) >= max_evaluations
    )
    if max_evaluations_is_reached:
        logger.debug("Max evaluations is reached")
        return True
    return False


def _sample_config(base_result_directory, sampler):
    previous_results, pending_configs, pending_configs_free = load_state(
        base_result_directory
    )
    logger.info(
        f"Read in {len(previous_results)} previous results and "
        f"{len(pending_configs)} pending evaluations "
        f"({len(pending_configs_free)} without a worker)"
    )

    if pending_configs_free:
        logger.debug("Sampling a pending config without a worker")
        config_id, config = more_itertools.first(pending_configs_free.items())
        config_working_directory = base_result_directory / f"config_{config_id}"
        previous_config_id_file = config_working_directory / "previous_config.id"
        if previous_config_id_file.exists():
            previous_config_id = previous_config_id_file.read_text()
        else:
            previous_config_id = None
    else:
        logger.debug("Sampling a new configuration")
        sampler.load_results(previous_results, pending_configs)

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

    logger.info(f"Sampled config {config_id}")
    return config, config_working_directory, previous_working_directory


def _evaluate_config(
    config, config_working_directory, evaluation_fn, previous_working_directory
):
    config_id = config_working_directory.name[len("config_") :]
    logger.info(f"Start evaluating config {config_id}")
    try:
        result = evaluation_fn(
            config=config,
            config_working_directory=config_working_directory,
            previous_working_directory=previous_working_directory,
        )
    except Exception:
        logger.error("An error occured during evaluation")
        result = "error"

    with Path(config_working_directory, "result.dill").open("wb") as result_open:
        dill.dump(result, result_open)

    logger.info(f"Finished evaluating config {config_id}")


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
            logger.info("Maximum evaluation reached, shutting down")
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
                _evaluate_config(
                    config,
                    config_working_directory,
                    evaluation_fn,
                    previous_working_directory,
                )
                config_locker.release_lock()
        else:
            time.sleep(5)
