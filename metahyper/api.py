import logging
import logging.config
import time
from pathlib import Path

import metahyper.new_api
import metahyper.old_core.result as hpres
from metahyper.old_core import nameserver as hpns
from metahyper.old_core.master import Master
from metahyper.old_core.worker import Worker


def _run_worker(nic_name, run_id, run_pipeline, config_space, working_directory, logger_name):
    time.sleep(5)  # Short artificial delay to make sure the nameserver is already running
    host = metahyper.new_api._nic_name_to_host(  # pylint: disable=protected-access
        nic_name
    )
    w = Worker(
        run_pipeline,
        config_space=config_space,
        run_id=run_id,
        host=host,
        logger=logging.getLogger(f"{logger_name}.worker"),
    )
    w.load_nameserver_credentials(working_directory=str(working_directory))
    w.run(background=False)


def _run_master(
    sampler,
    nic_name,
    run_id,
    run_pipeline,
    config_space,
    working_directory,
    n_iterations,
    start_worker,
    logger_name,
    do_live_logging,
    overwrite_logging,
):
    nameserver = hpns.NameServer(
        run_id=run_id,
        working_directory=str(working_directory),
        nic_name=nic_name,
    )
    ns_host, ns_port = nameserver.start()
    if start_worker:
        w = Worker(
            run_pipeline,
            config_space=config_space,
            run_id=run_id,
            host=ns_host,
            nameserver=ns_host,
            nameserver_port=ns_port,
            logger=logging.getLogger(f"{logger_name}.worker"),
        )
        w.run(background=True)

    if do_live_logging:
        result_logger = hpres.JSONResultLogger(
            directory=working_directory, overwrite=overwrite_logging
        )
    else:
        result_logger = None

    optimizer = Master(
        run_id=run_id,
        sampler=sampler,
        configs_space=config_space,
        host=ns_host,
        nameserver=ns_host,
        nameserver_port=ns_port,
        logger=logging.getLogger(f"{logger_name}.master"),
        result_logger=result_logger,
    )

    try:
        result = optimizer.run(n_iterations)
    finally:
        optimizer.shutdown(shutdown_workers=True)
        nameserver.shutdown()

    return result


def run(
    sampler,
    run_pipeline,
    config_space,
    working_directory,
    n_iterations,
    start_master=True,
    start_worker=True,
    nic_name="lo",
    logger_name="meta_hyper",
    do_live_logging=True,
    overwrite_logging=False,
    development_stage_id=None,
    task_id=None,
):
    if development_stage_id is not None:
        working_directory = Path(working_directory) / f"dev_{development_stage_id}"
    if task_id is not None:
        working_directory = Path(working_directory) / f"task_{task_id}"

    run_id = str(working_directory).replace("/", "_")

    # TODO: log more arguments
    logger = logging.getLogger(logger_name)
    logger.info(f"Using working_directory={working_directory}")

    if start_master:
        result = _run_master(
            sampler,
            nic_name,
            run_id,
            run_pipeline,
            config_space,
            working_directory,
            n_iterations,
            start_worker,
            logger_name,
            do_live_logging,
            overwrite_logging,
        )
        logger.info(f"Run finished")
        return result
    elif start_worker:
        _run_worker(nic_name, run_id, run_pipeline, config_space, working_directory, logger_name)
        logger.info(f"Run finished")
    else:
        raise ValueError("Need to start either master or worker.")
