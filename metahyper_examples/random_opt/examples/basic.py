import ConfigSpace as CS

from metahyper_examples.random_opt.random_search import run_random_search


def run_pipeline(  # pylint: disable=unused-argument
    config, config_working_directory, previous_working_directory
):
    return config["counter"]


if __name__ == "__main__":
    # Needs to be the configspace type expected by the approach, here: random search
    config_space = CS.ConfigurationSpace()
    config_space.add_hyperparameter(
        CS.hyperparameters.UniformIntegerHyperparameter("counter", lower=1, upper=100)
    )
    run_random_search(
        run_pipeline=run_pipeline,
        config_space=config_space,
        working_directory="results/basic_example",
        n_iterations=10,
        overwrite_logging=True,
    )
