import time
import uuid


class Sampler:
    def __init__(self, config_space):
        self.config_space = config_space
        self.results = dict()

    def load_results(self, results, pending_configs):
        self.results = {**self.results, **results}

    def new_result(self, result, config_id):
        self.results[config_id] = result

    def get_config_and_ids(self):
        config = dict(a=len(self.results))
        config_id = str(uuid.uuid4())[:6]
        previous_config_id = None
        return config, config_id, previous_config_id


def evaluation_fn(config, config_working_directory, previous_working_directory):
    time.sleep(45)
    return "evald"


if __name__ == "__main__":
    import logging

    import metahyper

    logging.basicConfig(level=logging.INFO)

    config_space = None
    sampler = Sampler(config_space)
    opt_dir = "test_opt_dir"

    metahyper.run(evaluation_fn, sampler, optimization_dir=opt_dir, max_evaluations=5)
    previous_results, pending_configs, pending_configs_free = metahyper.read(opt_dir)
