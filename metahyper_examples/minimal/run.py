import time
import uuid


class Sampler:
    def __init__(self):
        self.results = dict()

    def load_results(self, results, pending_configs):
        self.results = results

    def get_config_and_ids(self):
        config = dict(a=len(self.results))
        config_id = str(uuid.uuid4())[:6]
        previous_config_id = None
        return config, config_id, previous_config_id


def evaluation_fn(working_directory, **config):
    time.sleep(15)
    return 5


if __name__ == "__main__":
    import logging

    import metahyper

    logging.basicConfig(level=logging.INFO)

    opt_dir = "test_opt_dir"
    metahyper.run(
        evaluation_fn, Sampler(), optimization_dir=opt_dir, max_evaluations_total=5
    )
    previous_results, pending_configs, _ = metahyper.read(opt_dir)
