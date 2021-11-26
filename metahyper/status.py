import pathlib
import pprint

import dill


def load_state(base_result_directory):
    previous_results = dict()
    pending_configs = dict()
    for config_dir in base_result_directory.iterdir():
        result_file = config_dir / "result.dill"
        worker_dead_file = config_dir / "worker.dead"
        config_file = config_dir / "config.dill"
        config_id = config_dir.name[len("config_") :]

        if result_file.exists():
            with result_file.open("rb") as results_file_stream:
                previous_results[config_id] = dill.load(results_file_stream)
        elif worker_dead_file.exists():
            pass  # TODO: handle master crashed before letting config restart
        else:
            # TODO: handle master crashed before config file created
            with config_file.open("rb") as config_file_stream:
                pending_configs[config_id] = dill.load(config_file_stream)

    return previous_results, pending_configs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=pathlib.Path)
    parser.add_argument("--configs", action="store_true")
    args = parser.parse_args()

    previous_results, pending_configs = load_state(args.run_directory / "results")
    print(f"#Evaluated configs: {len(previous_results)}")
    print(f"#Pending configs: {len(pending_configs)}")
    print(f"#Registered workers: unkown")
    print(f"#Master status: unkown")
    if args.configs:
        print()
        print("Evaluated configs:")
        pprint.pprint(previous_results)
