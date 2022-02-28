from __future__ import annotations

import inspect
import logging
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dill
import more_itertools

from ._locker import Locker


@dataclass
class ConfigResult:
    config: Any
    result: Any


class Sampler(ABC):
    def get_state(self) -> Any:  # pylint: disable=no-self-use
        return None

    def load_state(self, state: Any):
        pass

    def load_results(
        self, results: dict[Any, ConfigResult], pending_configs: dict[Any, ConfigResult]
    ) -> None:
        pass

    @abstractmethod
    def get_config_and_ids(self) -> tuple[Any, str, str | None]:
        """
        Returns:
            config
            config_id
            previous_config_id
        """
        raise NotImplementedError


def read(optimization_dir: Path | str, logger=None):
    base_result_directory = Path(optimization_dir) / "results"
    if logger is None:
        logger = logging.getLogger("metahyper")
    logger.debug(f"Loading results from {base_result_directory}")

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
            # Should probably warn the user somehow about this, although it is not
            # dangerous
            logger.info(f"Removing {config_dir} as worker died during config sampling.")
            # TODO: use shutil to remove recursively
            # OSError: [Errno 39] Directory not empty: [...]
            config_dir.rmdir()

    logger.debug(
        f"Read in {len(previous_results)} previous results and "
        f"{len(pending_configs)} pending evaluations "
        f"({len(pending_configs_free)} without a worker)"
    )
    logger.debug(
        f"Read in previous_results={previous_results}, "
        f"pending_configs={pending_configs}, "
        f"and pending_configs_free={pending_configs_free}, "
    )
    return previous_results, pending_configs, pending_configs_free


def _check_max_evaluations(
    optimization_dir, max_evaluations, logger, continue_until_max_evaluation_completed
):
    logger.debug("Checking if max evaluations is reached")
    previous_results, pending_configs, _ = read(optimization_dir, logger)

    if continue_until_max_evaluation_completed:
        max_reached = len(previous_results) >= max_evaluations
    else:
        max_reached = len(previous_results) + len(pending_configs) >= max_evaluations

    if max_reached:
        logger.debug("Max evaluations is reached")

    return max_reached


def _sample_config(optimization_dir, sampler, logger):
    # First load the results and state of the optimizer
    previous_results, pending_configs, pending_configs_free = read(
        optimization_dir, logger
    )
    optimizer_state_file = optimization_dir / ".optimizer_state.dill"
    if optimizer_state_file.exists():
        with optimizer_state_file.open("rb") as optimizer_state_stream:
            optimizer_state = dill.load(optimizer_state_stream)
        sampler.load_state(optimizer_state)

    # Then, either:
    # If: Sample a previously sampled config that is now without worker
    # Else: Sample according to the sampler
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

        Path(config_working_directory, "time_sampled.txt").write_text(
            str(time.time()), encoding="utf-8"
        )
        if previous_config_id is not None:
            previous_config_id_file = config_working_directory / "previous_config.id"
            previous_config_id_file.write_text(previous_config_id)

    if previous_config_id is not None:
        previous_working_directory = Path(
            base_result_directory, f"config_{previous_config_id}"
        )
    else:
        previous_working_directory = None

    # Finally, save the sampled config and the state of the optimizer to disk:

    logger.debug("Getting state from sampler")
    optimizer_state = sampler.get_state()
    if optimizer_state is not None:
        logger.debug("State was not None, so now serialize it")
        with optimizer_state_file.open("wb") as optimizer_state_stream:
            dill.dump(optimizer_state, optimizer_state_stream)

    # We want this to be the last action in sampling to catch potential crashes
    with Path(config_working_directory, "config.dill").open("wb") as config_stream:
        dill.dump(config, config_stream)

    logger.debug(f"Sampled config {config_id}")
    return config, config_working_directory, previous_working_directory


def _evaluate_config(
    config,
    working_directory,
    evaluation_fn,
    previous_working_directory,
    logger,
    evaluation_fn_args,
    evaluation_fn_kwargs,
    post_evaluation_hook,
):
    # First, the actual evaluation along with error handling and support of multiple APIs
    config_id = working_directory.name[len("config_") :]
    logger.info(f"Start evaluating config {config_id}")
    try:
        # API support: If working_directory and previous_working_directory are included
        # in the signature we supply their values, otherwise we simply do nothing.
        evaluation_fn_params = inspect.signature(evaluation_fn).parameters
        directory_params = []
        if "working_directory" in evaluation_fn_params:
            directory_params.append(working_directory)
        if "previous_working_directory" in evaluation_fn_params:
            directory_params.append(previous_working_directory)

        # API support: Allow config to be used as:
        try:
            # 1. Individual keyword arguments
            # 2. Allowed to be captured as **configs
            result = evaluation_fn(
                *directory_params,
                *evaluation_fn_args,
                **evaluation_fn_kwargs,
                **config,
            )
        except TypeError:
            # 3. As a mere single keyword argument
            result = evaluation_fn(
                *directory_params,
                *evaluation_fn_args,
                **evaluation_fn_kwargs,
                config=config,
            )
    except Exception:
        logger.exception(
            f"An error occured during evaluation of config {config_id}: " f"{config}."
        )
        result = "error"
    except KeyboardInterrupt as e:
        raise e

    # Finally, we now dump all information to disk:
    # 1. When was the evaluation completed
    Path(working_directory, "time_end.txt").write_text(str(time.time()), encoding="utf-8")

    # 2. The result returned by the evaluation_fn
    with Path(working_directory, "result.dill").open("wb") as result_open:
        dill.dump(result, result_open)

    # 3. Anything the user might want to serialize (or do otherwise)
    if post_evaluation_hook is not None:
        post_evaluation_hook(config, config_id, working_directory, result, logger)
    else:
        logger.info(f"Finished evaluating config {config_id}")


def run(
    evaluation_fn,
    sampler,
    optimization_dir,
    max_evaluations_total=None,
    max_evaluations_per_run=None,
    continue_until_max_evaluation_completed=False,
    development_stage_id=None,
    task_id=None,
    logger=None,
    evaluation_fn_args=None,
    evaluation_fn_kwargs=None,
    post_evaluation_hook=None,
    overwrite_optimization_dir=False,
):
    if logger is None:
        logger = logging.getLogger("metahyper")

    if evaluation_fn_args is None:
        evaluation_fn_args = list()
    if evaluation_fn_kwargs is None:
        evaluation_fn_kwargs = dict()

    optimization_dir = Path(optimization_dir)
    if overwrite_optimization_dir and optimization_dir.exists():
        logger.warning("Overwriting working_directory")
        shutil.rmtree(optimization_dir)

    if development_stage_id is not None:
        optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    if task_id is not None:
        optimization_dir = Path(optimization_dir) / f"task_{task_id}"

    base_result_directory = optimization_dir / "results"
    base_result_directory.mkdir(parents=True, exist_ok=True)

    decision_lock_file = optimization_dir / ".decision_lock"
    decision_lock_file.touch(exist_ok=True)
    decision_locker = Locker(decision_lock_file, logger.getChild("_locker"))

    evaluations_in_this_run = 0
    while True:
        if max_evaluations_total is not None and _check_max_evaluations(
            optimization_dir,
            max_evaluations_total,
            logger,
            continue_until_max_evaluation_completed,
        ):
            logger.info("Maximum total evaluations is reached, shutting down")
            break

        if (
            max_evaluations_per_run is not None
            and evaluations_in_this_run >= max_evaluations_per_run
        ):
            logger.info("Maximum evaluations per run is reached, shutting down")
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
                    evaluation_fn_args,
                    evaluation_fn_kwargs,
                    post_evaluation_hook,
                )
                config_locker.release_lock()
                evaluations_in_this_run += 1
        else:
            time.sleep(5)
