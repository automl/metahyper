import copy
import logging
import os
import threading
import time
from copy import deepcopy

import numpy as np

from metahyper import Dispatcher, Result, SuccessiveHalving


class Master:
    def __init__(
        self,
        run_id,
        sampler,
        configs_space,
        working_directory=".",
        ping_interval=60,
        nameserver="127.0.0.1",
        nameserver_port=None,
        host=None,
        job_queue_sizes=(-1, 0),
        dynamic_queue_size=True,
        logger=None,
        result_logger=None,
    ):
        r"""
        The Master class is responsible for the book keeping and to decide what to run
        next. Optimizers are instantiations of Master, that handle the important steps of
        deciding what configurations to run on what budget when.

        Parameters
        ----------
        run_id : string
            A unique identifier of that Hyperband run. Use, for example, the cluster's
            JobID when running multiple concurrent runs to separate them
        working_directory: string
            The top level working directory accessible to all compute nodes at shared
            filesystem.
        ping_interval: int
            number of seconds between pings to discover new nodes. Default is 60 seconds.
        nameserver: str
            address of the Pyro4 nameserver
        nameserver_port: int
            port of Pyro4 nameserver
        host: str
            ip (or name that resolves to that) of the network interface to use
        job_queue_size: tuple of ints
            min and max size of the job queue. During the run, when the number of jobs in
            the queue reaches the min value, it will be filled up to the max size.
            Default: (0,1)
        dynamic_queue_size: bool
            Whether or not to change the queue size based on the number of workers
            available. If true (default), the job_queue_sizes are relative to the current
            number of workers.
        logger: logging.logger like object
            the logger to output some (more or less meaningful) information
        result_logger: hp_transfer_optimizer.api.results.util.JSONResultLogger object
            a result logger that writes live results to disk
        """

        self.config_space = configs_space
        self._sampler = sampler
        self.working_directory = working_directory
        os.makedirs(self.working_directory, exist_ok=True)

        if logger is None:
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = logger

        self.result_logger = result_logger

        self.time_ref = None

        self.iterations = []
        self.jobs = []

        self.num_running_jobs = 0
        self.job_queue_sizes = job_queue_sizes
        self.user_job_queue_sizes = job_queue_sizes
        self.dynamic_queue_size = dynamic_queue_size

        if job_queue_sizes[0] >= job_queue_sizes[1]:
            raise ValueError("The queue size range needs to be (min, max) with min<max!")

        # condition to synchronize the job_callback and the queue
        self.thread_cond = threading.Condition()

        self.config = {"time_ref": self.time_ref}

        self.dispatcher = Dispatcher(
            self.job_callback,
            queue_callback=self.adjust_queue_size,
            run_id=run_id,
            ping_interval=ping_interval,
            nameserver=nameserver,
            nameserver_port=nameserver_port,
            host=host,
        )

        self.dispatcher_thread = threading.Thread(target=self.dispatcher.run)
        self.dispatcher_thread.start()

        # Hyperband related stuff from original hpbandster code
        self.eta = eta = 3
        self.min_budget = min_budget = 1
        self.max_budget = max_budget = 1
        self.max_SH_iter = -int(np.log(min_budget / max_budget) / np.log(eta)) + 1
        self.budgets = max_budget * np.power(
            eta, -np.linspace(self.max_SH_iter - 1, 0, self.max_SH_iter)
        )

        self.config.update(
            {
                "eta": eta,
                "min_budget": min_budget,
                "max_budget": max_budget,
                "budgets": self.budgets,
                "max_SH_iter": self.max_SH_iter,
            }
        )

    def shutdown(self, shutdown_workers=False):
        self.logger.debug(
            f"shutdown initiated, shutdown_workers = {str(shutdown_workers)}"
        )
        self.dispatcher.shutdown(shutdown_workers)
        self.dispatcher_thread.join()

    def wait_for_workers(self, min_n_workers=1):
        """
        helper function to hold execution until some workers are active

        Parameters
        ----------
        min_n_workers: int
            minimum number of workers present before the run starts
        """

        self.logger.debug("wait_for_workers trying to get the condition")
        with self.thread_cond:
            while self.dispatcher.number_of_workers() < min_n_workers:
                self.logger.debug(
                    f"only {self.dispatcher.number_of_workers():d} worker(s) available, "
                    f"waiting for at least {min_n_workers:d}."
                )
                self.thread_cond.wait(1)
                self.dispatcher.trigger_discover_worker()

        self.logger.debug("Enough workers to start this run!")

    def get_next_iteration(self, iteration, iteration_kwargs=None):
        if iteration_kwargs is None:
            iteration_kwargs = {}
        s = self.max_SH_iter - 1 - (iteration % self.max_SH_iter)
        n0 = int(np.floor(self.max_SH_iter / (s + 1)) * self.eta ** s)
        ns = [max(int(n0 * (self.eta ** (-i))), 1) for i in range(s + 1)]

        return SuccessiveHalving(
            HPB_iter=iteration,
            num_configs=ns,
            budgets=self.budgets[(-s - 1) :],
            config_sampler=self._sampler.get_config,
            **iteration_kwargs,
        )

    def _run(
        self,
        n_iterations=1,
        min_n_workers=1,
        **iteration_kwargs,
    ):
        """
            run n_iterations of SuccessiveHalving

        Parameters
        ----------
        task:
        n_iterations: int
            number of iterations to be performed in this run
        min_n_workers: int
            minimum number of workers before starting the run
        iteration_kwargs:
        """
        if self._sampler is None:
            raise ValueError("Need to set a _sampler in run")

        self.wait_for_workers(min_n_workers)

        iteration_kwargs.update({"result_logger": self.result_logger})

        if self.time_ref is None:
            self.time_ref = time.time()
            self.config["time_ref"] = self.time_ref

            self.logger.info(f"starting run at {str(self.time_ref)}")

        self.thread_cond.acquire()
        while True:
            self._queue_wait()

            # find a new run to schedule
            next_run = None
            iteration_id = None
            for iteration_id in self.active_iterations():
                next_run = self.iterations[iteration_id].get_next_run()
                if next_run is not None:
                    break

            if next_run is not None:
                self.logger.debug(f"schedule new run for iteration {iteration_id:d}")
                self._submit_job(*next_run)
                continue
            elif n_iterations > 0:  # we might be able to start the next iteration
                self.iterations.append(
                    self.get_next_iteration(len(self.iterations), iteration_kwargs)
                )
                n_iterations -= 1
                continue

            # at this point there is no immediate run that can be scheduled,
            # so wait for some job to finish if there are active iterations
            if self.active_iterations():
                self.thread_cond.wait()
            else:
                break

        # Wait for all iterations to finish
        while len(self.dispatcher.running_jobs) > 0:
            self.thread_cond.wait()

        self.thread_cond.release()
        return Result(
            [copy.deepcopy(i.data) for i in self.iterations],
            self.config,
        )

    def run(
        self,
        n_iterations,
        **kwargs,
    ):
        result = self._run(
            n_iterations=n_iterations,
            **kwargs,
        )
        self.iterations.clear()
        return result

    def adjust_queue_size(self, number_of_workers=None):
        self.logger.debug(f"number of workers changed to {str(number_of_workers)}")
        with self.thread_cond:
            self.logger.debug("adjust_queue_size: lock acquired")
            if self.dynamic_queue_size:
                nw = (
                    self.dispatcher.number_of_workers()
                    if number_of_workers is None
                    else number_of_workers
                )
                self.job_queue_sizes = (
                    self.user_job_queue_sizes[0] + nw,
                    self.user_job_queue_sizes[1] + nw,
                )
                self.logger.info(f"adjusted queue size to {str(self.job_queue_sizes)}")
            self.thread_cond.notify_all()

    def job_callback(self, job):
        """
        method to be called when a job has finished

        this will do some book keeping and call the user defined
        new_result_callback if one was specified
        """
        self.logger.debug(f"job_callback for {str(job.id)} started")
        with self.thread_cond:
            self.logger.debug(f"job_callback for {str(job.id)} got condition")
            self.num_running_jobs -= 1

            if self.result_logger is not None:
                self.result_logger(job)
            self.iterations[job.id[0]].register_result(job)
            _config = deepcopy(self.config_space)
            _config.create_from_id(job.kwargs["config"])
            job.kwargs["config"] = _config
            self._sampler.new_result(job)

            if self.num_running_jobs <= self.job_queue_sizes[0]:
                self.logger.debug("Trying to run another job!")
                self.thread_cond.notify()

        self.logger.debug(f"job_callback for {str(job.id)} finished")

    def _queue_wait(self):
        """
        helper function to wait for the queue to not overflow/underload it
        """

        if self.num_running_jobs >= self.job_queue_sizes[1]:
            while self.num_running_jobs > self.job_queue_sizes[0]:
                self.logger.debug(
                    f"running jobs: {self.num_running_jobs:d}, "
                    f"queue sizes: {str(self.job_queue_sizes)} -> wait"
                )
                self.thread_cond.wait()

    def _submit_job(self, config_id, config, budget):
        """
        hidden function to submit a new job to the dispatcher

        This function handles the actual submission in a
        (hopefully) thread save way
        """
        self.logger.debug(f"trying submitting job {str(config_id)} to dispatcher")
        with self.thread_cond:
            self.logger.debug(f"submitting job {str(config_id)} to dispatcher")
            config_id_str = "_".join(map(str, config_id))
            config_working_directory = os.path.join(self.working_directory, config_id_str)
            self.dispatcher.submit_job(
                config_id,
                config=config,
                budget=budget,
                config_working_directory=config_working_directory,
            )
            self.num_running_jobs += 1

        # shouldn't the next line be executed while holding the condition?
        self.logger.debug(f"job {str(config_id)} submitted to dispatcher")

    def active_iterations(self):
        """
        function to find active (not marked as finished) iterations

        Returns
        -------
            list: all active iteration objects (empty if there are none)
        """
        return list(
            filter(
                lambda idx: not self.iterations[idx].is_finished,
                range(len(self.iterations)),
            )
        )

    def __del__(self):
        pass
