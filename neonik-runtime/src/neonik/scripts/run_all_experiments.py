import json
import os
import random
import sys
import traceback
from argparse import ArgumentParser
from datetime import datetime, UTC
import subprocess

import humanize
import yaml

from neonik.neon.neonconfig import NeonConfig
from neonik.neon.neonhandler import NeonHandler
from neonik.neon.operationmode import OperationMode
from neonik.spec import ProjectSpec, ExperimentSpec
from neonik.experiment_setup import neonik_setup_experiment, execute_experiment


def check_for_git_changes() -> bool:
    res = subprocess.run(["git", "status", "-s"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return len(res.stdout) > 0 or len(res.stderr) > 0 


def determine_git_version() -> str | None:
    try:
        git_output = subprocess.run(["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        commit_hash = git_output.stdout or b""
        commit_hash = commit_hash.decode(errors='ignore').strip('\n ')

        if check_for_git_changes():
            while True:
                print("Uncommited changes were detected, leading to potentially invalid version in the computation reports.")
                print("Your options now are:")
                print("1: Abort, commit changes, and run this script again")
                print("2: Proceed without storing any version information.")
                print("3: Proceed with the current git commit as version information")
                response = input("Your choice [1]:").strip(' \n')
                match response:
                    case "1":
                        sys.exit(-1)
                    case "":
                        sys.exit(-1)
                    case "2":
                        return None
                    case "3":
                        return commit_hash
                    case _:
                        print("Invalid response.\n")
        return commit_hash
    except:
        return None
    


def run():

    parser = ArgumentParser(prog="python3 -m neonik.scripts.run-all-experiments")
    parser.add_argument("experiment_spec_file", type=str,
                        default="target-experiments.yml",
                        help="A YAML file containing the experiment specs to run. Default is \"target-experiments.yml\".")
    args = parser.parse_args()

    project_specification_file = "neonik-project.yml"
    target_experiment_file = args.experiment_spec_file
    timezone = None
    reports_folder = "evaluation-reports"

    custom_version = determine_git_version()

    try:
        with open(project_specification_file, 'r') as f:
            project_spec = ProjectSpec.parse_serialized(yaml.safe_load(f))
    except:
        print("Failed to parse project spec.")
        traceback.print_exc()
        return

    experiment_specs: list[ExperimentSpec] = []
    try:
        with open(target_experiment_file, 'r') as f:
            serialized_experiment_specs = yaml.safe_load(f)
        for spec in serialized_experiment_specs:
            spec = ExperimentSpec.parse_serialized(spec)
            spec.validate_against(project_spec)
            experiment_specs.append(spec)
    except:
        print("Failed to parse and / or to validate the experiment specifications.")
        traceback.print_exc()
        return

    print(f"Loaded {len(experiment_specs)} experiment specification(s).")

    experiment_spec_layers: list[list[ExperimentSpec]] = []
    for spec in experiment_specs:
        while len(experiment_spec_layers) < spec.number_of_executions:
            experiment_spec_layers.append([])
        for layer in range(spec.number_of_executions):
            experiment_spec_layers[layer].append(spec)
    experiment_specs_respecting_number_of_executions = []
    for layer in experiment_spec_layers:
        random.shuffle(layer)
        experiment_specs_respecting_number_of_executions.extend(layer)
    experiment_specs = experiment_specs_respecting_number_of_executions

    mode = OperationMode.LOCAL_VIRTUAL if os.getuid() == 0 else OperationMode.LOCAL
    if os.getenv("NEONIK_INSTALL_DIR", "") != "" or os.getenv("NEONIK_MPSPDZ_PATH", "") != "":
        config = NeonConfig.from_environment_vars()
    else:
        config = NeonConfig.from_config_files()
    neon = NeonHandler(mode, config)
    if len(experiment_specs) > 10:
        neon.set_print_timers_upon_deletion(False)

    start_time = datetime.now(timezone)
    for i, experiment_spec in enumerate(experiment_specs):
        experiment_reports_folder = os.path.join(reports_folder, experiment_spec.component_identifier)
        os.makedirs(experiment_reports_folder, exist_ok=True)

        print(f"[*] Running experiment {i + 1}/{len(experiment_specs)}")
        print(f"Current time: {datetime.now(timezone)}")
        if i > 0:
            average_time_per_experiment = (datetime.now(timezone) - start_time) / i
            remaining_time = (len(experiment_specs) - i) * average_time_per_experiment
            eta_time = datetime.now(timezone) + remaining_time
            print(f"Estimated remaining time: {humanize.naturaldelta(remaining_time)}, eta: {eta_time}")

        # TODO: Read from neonhandler directly for the case that the neon configurator messed with our precious settings.
        print(f"Experiment parameters:")
        print(f"    {experiment_spec.mpspdz_protocol.executable} with {neon.n_parties} parties")
        print(f"    network: {experiment_spec.network}")
        print(f"    component: {experiment_spec.component_identifier}")
        print(f"    custom parameters: {experiment_spec.component_properties}")

        report = execute_experiment(neon, experiment_spec).to_serializable_dict()
        report['experiment_spec'] = experiment_spec.serialize()
        report['custom_version'] = custom_version
        with open(os.path.join(experiment_reports_folder, f"computation-{datetime.now(UTC).isoformat()}.json"), 'w') as f:
            json.dump(report, f)

    print(f"[*] Finished computation at {datetime.now(timezone)}")


if __name__ == '__main__':
    run()