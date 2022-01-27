import collections
import inspect
import logging
import time
from pathlib import Path

import dill
import more_itertools

from metahyper._locker import Locker


def _check_max_evaluations(optimization_dir, max_evaluations, logger):
    logger.debug("Checking if max evaluations is reached")
    previous_results, *_ = read(optimization_dir, logger)
    max_evaluations_is_reached = (
        max_evaluations is not None and len(previous_results) >= max_evaluations
    )
    if max_evaluations_is_reached:
        logger.debug("Max evaluations is reached")
        return True
    return False


def _sample_config(optimization_dir, sampler, logger):
    previous_results, pending_configs, pending_configs_free = read(
        optimization_dir, logger
    )
    logger.info(
        f"Read in {len(previous_results)} previous results and "
        f"{len(pending_configs)} pending evaluations "
        f"({len(pending_configs_free)} without a worker)"
    )

    base_result_directory = optimization_dir / "results"

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
    config, working_directory, evaluation_fn, previous_working_directory, logger
):
    config_id = working_directory.name[len("config_") :]
    logger.info(f"Start evaluating config {config_id}")
    try:
        evaluation_fn_params = inspect.signature(evaluation_fn).parameters
        directory_params = dict()
        if "working_directory" in evaluation_fn_params:
            directory_params["working_directory"] = working_directory
        if "previous_working_directory" in evaluation_fn_params:
            directory_params["previous_working_directory"] = previous_working_directory

        if isinstance(config, dict):
            result = evaluation_fn(
                **directory_params,
                **config,
            )
        else:
            result = evaluation_fn(
                **directory_params,
                config=config,
            )
    except Exception:
        logger.exception(f"An error occured during evaluation of config {config}:")
        result = "error"
    except KeyboardInterrupt as e:
        raise e

    with Path(working_directory, "result.dill").open("wb") as result_open:
        dill.dump(result, result_open)

    logger.info(f"Finished evaluating config {config_id}")


ConfigResult = collections.namedtuple("ConfigResult", ["config", "result"])


def read(optimization_dir, logger=None):
    base_result_directory = Path(optimization_dir) / "results"
    if logger is None:
        logger = logging.getLogger("metahyper")
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
                result = dill.load(results_file_stream)
            with config_file.open("rb") as config_file_stream:
                config = dill.load(config_file_stream)
            previous_results[config_id] = ConfigResult(config, result)

        elif config_file.exists():
            with config_file.open("rb") as config_file_stream:
                pending_configs[config_id] = dill.load(config_file_stream)

            config_lock_file = config_dir / ".config_lock"
            config_locker = Locker(config_lock_file, logger.getChild("_locker"))
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


def run(
    evaluation_fn,
    sampler,
    optimization_dir,
    development_stage_id=None,
    task_id=None,
    max_evaluations=None,
    logger=None,
):
    if logger is None:
        logger = logging.getLogger("metahyper")

    optimization_dir = Path(optimization_dir)
    if development_stage_id is not None:
        optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    if task_id is not None:
        optimization_dir = Path(optimization_dir) / f"task_{task_id}"

    base_result_directory = optimization_dir / "results"
    base_result_directory.mkdir(parents=True, exist_ok=True)

    decision_lock_file = optimization_dir / ".decision_lock"
    decision_lock_file.touch(exist_ok=True)
    decision_locker = Locker(decision_lock_file, logger.getChild("_locker"))

    while True:
        if max_evaluations is not None and _check_max_evaluations(
            optimization_dir, max_evaluations, logger
        ):
            logger.info("Maximum evaluation reached, shutting down")
            break

        if decision_locker.acquire_lock():
            config, working_directory, previous_working_directory = _sample_config(
                optimization_dir, sampler, logger
            )

            config_lock_file = working_directory / ".config_lock"
            config_lock_file.touch(exist_ok=True)
            config_locker = Locker(config_lock_file, logger.getChild("_locker"))
            config_lock_acquired = config_locker.acquire_lock()
            decision_locker.release_lock()
            if config_lock_acquired:
                _evaluate_config(
                    config,
                    working_directory,
                    evaluation_fn,
                    previous_working_directory,
                    logger,
                )
                config_locker.release_lock()
        else:
            time.sleep(5)
