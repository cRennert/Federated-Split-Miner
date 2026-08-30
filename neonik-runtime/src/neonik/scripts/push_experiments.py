import asyncio
import traceback
from argparse import ArgumentParser

import aiofiles
import yaml

from ..script_utils import NeonikCredentials, upload_experiments
from ..spec import ExperimentSpec, ProjectSpec


async def load_and_validate_experiment_specs(project_spec_file: str, experiements_file: str) -> list[ExperimentSpec] | None:
    try:
        async with aiofiles.open(project_spec_file, 'r') as f:
            project_spec = ProjectSpec.parse_serialized(yaml.safe_load(await f.read()))
    except:
        print("Failed to parse project spec.")
        traceback.print_exc()
        return None

    # TODO: Detect mismatch between online and offline version of the project spec.

    experiment_specs: list[ExperimentSpec] = []
    try:
        async with aiofiles.open(experiements_file, 'r') as f:
            serialized_experiment_specs = yaml.safe_load(await f.read())
        for raw_spec in serialized_experiment_specs:
            spec = ExperimentSpec.parse_serialized(raw_spec)
            spec.validate_against(project_spec)
            experiment_specs.append(spec)
    except:
        print("Failed to parse and / or to validate the experiment specifications.")
        traceback.print_exc()
        return None
    return experiment_specs


async def run():
    parser = ArgumentParser()
    parser.add_argument("--experiments", "-e", dest="target_experiments_path", default="target-experiments.yml",
                        help="Path to a YAML file containing the experiment specifications to be pushed. Due to the somewhat idempotent design of the backend, it is totally fine for the file to contain experiments already known to the server. In fact, it is considered best-practice to keep all \"targeted experiments\" in the file.")
    parser.add_argument("--update-computations", "-u", dest="update_computations", action="store_true",
                        help="Whether experiments should be re-run if their program hash has changed.")
    parser.add_argument("--update-failed", dest="update_failed", action="store_true",
                        help="Push as usual (without re-running for normal experiments), but also re-run failed experiments.")
    parser.add_argument("--project-spec", "-p", dest="project_spec_path", type=str, default="neonik-project.yml",
                        help="The path to the project spec. The Neon backend currently does not support non-standard project spec paths.")
    args = parser.parse_args()

    project_specification_file = args.project_spec_path
    target_experiments_file = args.target_experiments_path
    update_computations = args.update_computations
    update_failed = args.update_failed

    creds = await NeonikCredentials.from_config()

    experiment_specs = await load_and_validate_experiment_specs(project_specification_file, target_experiments_file)
    if experiment_specs is None:
        return

    await upload_experiments(creds, experiment_specs, update_computations, update_failed)

def main():
    # For UV script
    asyncio.run(run())

if __name__ == "__main__":
    main()