from __future__ import annotations

import inspect
import logging
import shutil
import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import more_itertools

from ._locker import Locker
from .utils import SerializerMapping, find_files, instance_from_map


@dataclass
class ConfigResult:
    config: Any
    result: Any


class Sampler(ABC):
    # pylint: disable=no-self-use,unused-argument

    def get_state(self) -> Any:
        """Return a state for the sampler that will be used in every other thread"""
        return

    def load_state(self, state: Any):  # pylint: disable
        """Load a state for the sampler shared accross threads"""

    def load_results(
        self, results: dict[Any, ConfigResult], pending_configs: dict[Any, ConfigResult]
    ) -> None:
        return

    @abstractmethod
    def get_config_and_ids(self) -> tuple[Any, str, str | None]:
        """Sample a new configuration

        Returns:
            config: serializable object representing the configuration
            config_id: unique identifier for the configuration
            previous_config_id: if provided, id of a previous on which this
                configuration is based
        """
        raise NotImplementedError

    def load_config(self, config: Any):  # pylint: disable=no-self-use
        """Transform a serialized object into a configuration object"""
        return config


def _load_sampled_paths(optimization_dir: Path | str, serializer, logger):
    optimization_dir = Path(optimization_dir)
    base_result_directory = optimization_dir / "results"
    logger.debug(f"Loading results from {base_result_directory}")

    previous_paths, pending_paths = {}, {}
    for config_dir in base_result_directory.iterdir():
        if not config_dir.is_dir():
            continue
        config_id = config_dir.name[len("config_") :]
        config_file = config_dir / f"config{serializer.SUFFIX}"
        result_file = config_dir / f"result{serializer.SUFFIX}"

        if result_file.exists():
            previous_paths[config_id] = (config_dir, config_file, result_file)
        elif config_file.exists():
            pending_paths[config_id] = (config_dir, config_file)
        else:
            existing_config = find_files(
                config_dir, ["config"], any_suffix=True, check_nonempty=True
            )
            if existing_config:
                existing_format = existing_config[0].suffix
                logger.warning(
                    f"Found directory {config_dir} with file {existing_config[0].name}. But function was called with the serializer for '{serializer.SUFFIX}' files, not '{existing_format}'."
                )
            else:
                # Should probably warn the user somehow about this, although it is not dangerous
                logger.info(
                    f"Removing {config_dir} as worker died during config sampling."
                )
                try:
                    shutil.rmtree(str(config_dir))
                except Exception as e:  # The worker doesn't need to crash for this
                    logger.error(f"Can't delete {config_dir}: {e}")
    return previous_paths, pending_paths


def read(optimization_dir: Path | str, serializer: str | Any = None, logger=None):
    optimization_dir = Path(optimization_dir)

    # Try to guess the serialization method used
    optimization_dir = Path(optimization_dir)
    if serializer is None:
        for name, serializer_cls in SerializerMapping.items():
            data_files = [
                f".optimizer_state{serializer_cls.SUFFIX}",
                f"config{serializer_cls.SUFFIX}",
                f"result{serializer_cls.SUFFIX}",
            ]
            if find_files(optimization_dir, data_files):
                serializer = name
                logging.info(f"Auto-detected {name} format for serializer")
                break
        else:
            serializer = "json"
            logging.info(f"Will use the {serializer} serializer as a default")

    serializer = instance_from_map(SerializerMapping, serializer, "serializer")
    if logger is None:
        logger = logging.getLogger("metahyper")

    previous_paths, pending_paths = _load_sampled_paths(
        optimization_dir, serializer, logger
    )
    previous_results, pending_configs, pending_configs_free = {}, {}, {}

    for config_id, (config_dir, config_file, result_file) in previous_paths.items():
        config = serializer.load_config(config_file)
        result = serializer.load(result_file)
        previous_results[config_id] = ConfigResult(config, result)

    for config_id, (config_dir, config_file) in pending_paths.items():
        pending_configs[config_id] = serializer.load_config(config_file)

        config_lock_file = config_dir / ".config_lock"
        config_locker = Locker(config_lock_file, logger.getChild("_locker"))
        if config_locker.acquire_lock():
            pending_configs_free[config_id] = pending_configs[config_id]

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
    optimization_dir,
    max_evaluations,
    serializer,
    logger,
    continue_until_max_evaluation_completed,
):
    logger.debug("Checking if max evaluations is reached")

    previous_paths, pending_paths = _load_sampled_paths(
        optimization_dir, serializer, logger
    )
    evaluation_count = len(previous_paths)

    # Taking into account pending evaluations
    if not continue_until_max_evaluation_completed:
        evaluation_count += len(pending_paths)

    if evaluation_count >= max_evaluations:
        logger.debug("Max evaluations is reached")

    return evaluation_count >= max_evaluations


def _sample_config(optimization_dir, sampler, serializer, logger):
    # First load the results and state of the optimizer
    previous_results, pending_configs, pending_configs_free = read(
        optimization_dir, serializer, logger
    )
    optimizer_state_file = optimization_dir / f".optimizer_state{serializer.SUFFIX}"
    if optimizer_state_file.exists():
        sampler.load_state(serializer.load(optimizer_state_file))

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
        serializer.dump(optimizer_state, optimizer_state_file)

    # We want this to be the last action in sampling to catch potential crashes
    serializer.dump(config, config_working_directory / f"config{serializer.SUFFIX}")

    logger.debug(f"Sampled config {config_id}")
    return config, config_working_directory, previous_working_directory


def _evaluate_config(
    config,
    working_directory,
    evaluation_fn,
    previous_working_directory,
    serializer,
    logger,
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
                **config,
            )
        except TypeError:  # TODO : remove this part (deprecated part)
            # 3. As a mere single keyword argument
            result = evaluation_fn(
                *directory_params,
                config=config,
            )
            warnings.warn(
                "Using the config argument for the evaluation function will"
                "soon be removed. Please use keyword arguments, or catch the"
                "config with '**config'.",
                FutureWarning,
            )
    except Exception:
        logger.exception(
            f"An error occured during evaluation of config {config_id}: " f"{config}."
        )
        result = "error"

    # Finally, we now dump all information to disk:
    # 1. When was the evaluation completed
    Path(working_directory, "time_end.txt").write_text(str(time.time()), encoding="utf-8")

    # 2. The result returned by the evaluation_fn
    serializer.dump(result, working_directory / f"result{serializer.SUFFIX}")

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
    development_stage_id=None,  # pylint: disable=unused-argument
    task_id=None,  # pylint: disable=unused-argument
    serializer: str | Any = "yaml",
    logger=None,
    post_evaluation_hook=None,
    overwrite_optimization_dir=False,
):
    serializer_cls = instance_from_map(
        SerializerMapping, serializer, "serializer", as_class=True
    )
    serializer = serializer_cls(sampler.load_config)
    if logger is None:
        logger = logging.getLogger("metahyper")

    optimization_dir = Path(optimization_dir)
    if overwrite_optimization_dir and optimization_dir.exists():
        logger.warning("Overwriting working_directory")
        shutil.rmtree(optimization_dir)

    # TODO
    # if development_stage_id is not None:
    #     optimization_dir = Path(optimization_dir) / f"dev_{development_stage_id}"
    # if task_id is not None:
    #     optimization_dir = Path(optimization_dir) / f"task_{task_id}"

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
            serializer,
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
                optimization_dir, sampler, serializer, logger
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
                    serializer,
                    logger,
                    post_evaluation_hook,
                )
                config_locker.release_lock()
                evaluations_in_this_run += 1
        else:
            time.sleep(5)
