import time
import uuid


class Sampler:
    def __init__(self, config_space):
        self.config_space = config_space
        self.results = []

    def new_result(self, result):
        self.results.append(result)

    def get_config_and_id(self):
        config_id = str(uuid.uuid4())[:6]
        return dict(a=len(self.results)), config_id


def evaluation_fn(config, config_working_directory, previous_working_directory):
    time.sleep(60)
    return "evald"


if __name__ == "__main__":
    import logging

    import metahyper

    logging.basicConfig(level=logging.INFO)
    metahyper.run(evaluation_fn, Sampler(dict()), optimization_dir="test_opt_dir")
