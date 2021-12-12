import argparse
import pathlib
import pprint

from metahyper.api import read

parser = argparse.ArgumentParser()
parser.add_argument("run_directory", type=pathlib.Path)
parser.add_argument("--configs", action="store_true")
args = parser.parse_args()

previous_results, pending_configs, pending_configs_free = read(args.run_directory)
print(f"#Evaluated configs: {len(previous_results)}")
print(f"#Pending configs: {len(pending_configs)}")
print(f"#Pending configs without worker: {len(pending_configs_free)}")
if args.configs:
    print()
    print("Evaluated configs:")
    pprint.pprint(previous_results)
