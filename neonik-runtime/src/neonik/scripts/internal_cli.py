import argparse
import logging
import humanize
import json
import os
import sys
import tempfile
import time
import traceback
from subprocess import CalledProcessError
from typing import Dict, List
from neonik.additional_run_parameters import AdditionalRunParameters
import requests
import yaml
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi import Header
from pydantic import BaseModel, ConfigDict

from neonik.experiment_setup import neonik_setup_experiment, run_smpc_with_additional_parameters
from neonik.neon.helper import get_logger
from neonik.neon.neonconfig import NeonConfig, ReportVerbosityLevel
from neonik.neon.neonhandler import NeonHandler
from neonik.neon.operationmode import OperationMode
from neonik.spec import ExperimentSpec, ProjectSpec

logger = get_logger('InternalCLI')
logger.setLevel(logging.DEBUG)
backend_endpoint_prefix = os.getenv("BACKEND", "http://neonik-frontend-dev-backend-1:8000/api/v1")


class InternalCLI:
    @staticmethod
    def run():
        allowed_commands = [
            "get-experiment-hash",
            "compile-experiment",
            "run-experiment",
            "serve-neonhandler-state",
            "prepare-neonhandler-state",
        ]
        parser = argparse.ArgumentParser(
            description='Setup and management script for NEON.',
            usage=f'''python -m neonik.scripts.internal-cli <command> [args]

            Available commands: {', '.join(allowed_commands)}''')

        parser.add_argument('command')
        args = parser.parse_args(sys.argv[1:2])
        command: str = args.command
        command_function_name = command.replace('-', '_')
        if command not in allowed_commands or not hasattr(InternalCLI, command_function_name):
            logger.critical(f'Unrecognized command: {command}')
            parser.print_help()
            return

        getattr(InternalCLI, command_function_name)(sys.argv[2:])

    @staticmethod
    def get_experiment_hash(args: list[str]):
        parser = argparse.ArgumentParser(description='Calculate the MP-SPDZ program hash of an experiment.')
        parser.add_argument("--project-id", type=str, required=True, dest="project",
                            help="The ID of the project the experiment belongs to.")
        parser.add_argument("--experiment-id", type=str, required=True, dest='experiment',
                            help="The ID of the experiment.")
        parser.add_argument("--runtime-version", type=str, required=True, dest="git_commit",
                            help="The current version of the runtime in the form of a git commit hash.")
        parser.add_argument("--access-token", type=str, required=True, dest="access_token",
                            help="The access token to use for authentication towards the backend.")
        args = parser.parse_args(args)
        project_id = args.project
        experiment_id = args.experiment
        git_commit = args.git_commit
        access_token = args.access_token

        try:
            experiment, _ = CLIUtilities.get_and_validate_experiment_spec_from_backend(project_id, experiment_id,
                                                                                       access_token)
            neon, additional_parameters = CLIUtilities.setup_neon(experiment)
            if not experiment.trusted:
                CLIUtilities.ensure_additional_parameters_are_supported_for_untrusted_runs(additional_parameters)

            program_hash = neon.get_program_hash()
            logger.debug("Uploading program hash...")
            requests.put(f"{backend_endpoint_prefix}/project/{project_id}/experiments/{experiment_id}/hash",
                         json={'hash': program_hash, 'runtime_version': git_commit},
                         headers={"Authorization": f"Bearer {access_token}"})
            logger.debug(" ... done")
        except BaseException as e:
            CLIUtilities.send_error(project_id, experiment_id, access_token, "get_hash", e)

    @staticmethod
    def compile_experiment(args: list[str]):
        parser = argparse.ArgumentParser(description='Calculate the MP-SPDZ program hash of an experiment.')
        parser.add_argument("--project-id", type=str, required=True, dest="project",
                            help="The ID of the project the experiment belongs to.")
        parser.add_argument("--experiment-id", type=str, required=True, dest='experiment',
                            help="The ID of the experiment.")
        parser.add_argument("--access-token", type=str, required=True, dest="access_token",
                            help="The access token to use for authentication towards the backend.")
        args = parser.parse_args(args)
        project_id = args.project
        experiment_id = args.experiment
        access_token = args.access_token

        try:
            experiment, expected_program_hash = CLIUtilities.get_and_validate_experiment_spec_from_backend(project_id,
                                                                                                           experiment_id,
                                                                                                           access_token)
            neon, _ = CLIUtilities.setup_neon(experiment)
            program_hash = neon.get_program_hash()
            assert program_hash == expected_program_hash

            with tempfile.TemporaryDirectory() as tmp_dir:
                archive_filename = os.path.join(tmp_dir, "program.zip")
                print("Archive filename:", archive_filename)

                neon.compile()
                logger.debug("Compressing programm...")
                neon.compress_compiled_program(archive_filename)
                logger.debug(" ... done")
                program_size = os.path.getsize(archive_filename)

                logger.debug(f"Uploading program ({humanize.naturalsize(program_size)} / {program_size} bytes) ...")

                response = CLIUtilities.send_with_retry(
                    requests.put,
                    f"{backend_endpoint_prefix}/project/{project_id}/programs/{program_hash}",
                    file_name="compiled_program",
                    file_path=archive_filename,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=(5,300) # Uploading large protocols might need more time.
                )
                logger.debug(f" ... done")

            assert response.status_code == 200, f"Failed to upload compiled program: {response.status} attempts."

        except BaseException as e:
            if isinstance(e, CalledProcessError):
                full_message = f"{e.cmd} returned exit code {e.returncode},\n\n{e.output}\n\n{e.stderr}"
                e = RuntimeError(full_message)
            CLIUtilities.send_error(project_id, experiment_id, access_token, "compile", e)

    @staticmethod
    def run_experiment(args: list[str]):
        parser = argparse.ArgumentParser(description="Run an experiment.")
        parser.add_argument(
            "--project-id",
            type=str,
            required=True,
            dest="project",
            help="The ID of the project the experiment belongs to.",
        )
        parser.add_argument(
            "--experiment-id",
            type=str,
            required=True,
            dest="experiment",
            help="The ID of the experiment.",
        )
        parser.add_argument(
            "--runtime-version",
            type=str,
            required=True,
            dest="git_commit",
            help="The current version of the runtime in the form of a git commit hash.",
        )
        parser.add_argument(
            "--trusted",
            type=str,
            required=True,
            dest="trusted",
            help="Run the experiment in trusted mode.",
        )
        parser.add_argument(
            "--access-token",
            type=str,
            required=True,
            dest="access_token",
            help="The access token to use for authentication towards the backend.",
        )

        parser.add_argument(
            "--neonhandler-state-dir",
            type=str,
            required=False,
            dest="neonhandler_state_dir",
            help="Directory containing prepared NeonHandler state.",
        )
        args = parser.parse_args(args)
        project_id = args.project
        experiment_id = args.experiment
        runtime_version = args.git_commit
        trusted = True if args.trusted.lower() == "true" else False
        access_token = args.access_token
        neonhandler_state_dir = args.neonhandler_state_dir

        try:
            if trusted:
                experiment, expected_program_hash = (
                    CLIUtilities.get_and_validate_experiment_spec_from_backend(
                        project_id, experiment_id, access_token
                    )
                )
                neon, additional_parameters = CLIUtilities.setup_neon(experiment)
                program_hash = neon.get_program_hash()
            else:
                experiment, expected_program_hash = (
                    CLIUtilities.get_experiment_spec_from_backend(
                        project_id, experiment_id, access_token
                    )
                )
                logger.debug("Getting NEON state")
                if neonhandler_state_dir is None:
                    raise RuntimeError(
                        "Untrusted run requires --neonhandler-state-dir."
                    )

                state_file = os.path.join(neonhandler_state_dir, "state.json")

                logger.debug(f"State directory contents: {os.listdir(neonhandler_state_dir)}")
                with open(state_file, "r") as f:
                    neonhandler_state = json.load(f)

                logger.debug("Successfully loaded state.json")

                print("NeonHandler state:", neonhandler_state)
                neon, program_hash, additional_parameters = CLIUtilities.untrusted_setup_neon(neonhandler_state)
                CLIUtilities.ensure_additional_parameters_are_supported_for_untrusted_runs(additional_parameters)
            assert program_hash == expected_program_hash

            # Download the program.
            logger.debug("Downloading program...")
            with tempfile.TemporaryDirectory() as tmp_dir:
                archive_filename = os.path.join(tmp_dir, "program.zip")

                # Chunked download of potentially large files, https://stackoverflow.com/a/16696317
                with requests.get(f"{backend_endpoint_prefix}/project/{project_id}/programs/{program_hash}",
                                  stream=True, headers={"Authorization": f"Bearer {access_token}"}) as r:
                    r.raise_for_status()
                    with open(archive_filename, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            # If you have chunk encoded response uncomment if
                            # and set chunk_size parameter to None.
                            # if chunk:
                            f.write(chunk)
                logger.debug(" ... done, now decompressing ...")

                neon.decompress_compiled_program(archive_filename)
                logger.debug(" ... done")

            neon.set_precompiled_program(program_hash)
            neon.set_report_verbosity(ReportVerbosityLevel.STDOUT)

            report = run_smpc_with_additional_parameters(neon, additional_parameters, skip_compile=True)

            if not report.run_was_successfull():
                CLIUtilities.send_error(
                    project_id,
                    experiment_id,
                    access_token,
                    "run",
                    RuntimeError(
                        f"MP-SPDZ run was unsuccessful.\n\n{report.client_reports[0].stdout.decode('utf8')}\n\n{report.client_reports[0].stderr.decode('utf-8')}"
                    ),
                )
                return
            logger.debug("Posting report...")
            response = CLIUtilities.send_with_retry(
                requests.post,
                f"{backend_endpoint_prefix}/project/{project_id}/experiments/{experiment_id}/reports/",
                json_data={
                    'report': report.reduce_verbosity().to_serializable_dict(),
                    'runtime_version': runtime_version
                },
                headers={"Authorization": f"Bearer {access_token}"}
            )
            logger.debug(" ... done")

            assert response.status_code == 200
        except BaseException as e:
            logger.exception("run_experiment failed.")
            CLIUtilities.send_error(project_id, experiment_id, access_token, "run", e)

    @staticmethod
    def serve_neonhandler_state(args: list[str]):
        parser = argparse.ArgumentParser(
            description="Run a small server providing serialized Neon-states for the experiment."
        )
        parser.add_argument(
            "--access-token",
            type=str,
            required=True,
            dest="access_token",
            help="The access token to use for authentication towards the backend.",
        )
        args = parser.parse_args(args)
        access_token = args.access_token

        # Start a simple HTTP server that serves the neon config file.
        app = CLIUtilities.neon_handler_state_server(access_token)
        uvicorn.run(app, host="0.0.0.0", port=8080, timeout_graceful_shutdown=60)

    @staticmethod
    def prepare_neonhandler_state(args: list[str]):
        parser = argparse.ArgumentParser(
            description="Prepare serialized NeonHandler state and write it to a shared volume."
        )
        parser.add_argument(
            "--project-id",
            type=str,
            required=True,
            dest="project",
            help="The ID of the project the experiment belongs to.",
        )
        parser.add_argument(
            "--experiment-id",
            type=str,
            required=True,
            dest="experiment",
            help="The ID of the experiment.",
        )
        parser.add_argument(
            "--access-token",
            type=str,
            required=True,
            dest="access_token",
            help="The access token to use for authentication towards the backend.",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            required=True,
            dest="output_dir",
            help="Directory where NeonHandler state should be written.",
        )
        args = parser.parse_args(args)
        access_token = args.access_token
        project_id = args.project
        experiment_id = args.experiment
        output_dir = args.output_dir

        os.makedirs(output_dir, exist_ok=True)

        experiment, _ = CLIUtilities.get_experiment_spec_from_backend(
            project_id, experiment_id, access_token
        )

        neonhandler, additional_parameters = CLIUtilities.setup_neon(experiment)
        if not experiment.trusted:
            CLIUtilities.ensure_additional_parameters_are_supported_for_untrusted_runs(additional_parameters)

        serialize_state = neonhandler.serialize_neon_handler_state()

        if serialize_state is None:
            raise RuntimeError("No NeonHandler state available.")

        output_file = os.path.join(output_dir, "state.json")

        with open(output_file, "w") as f:
            json.dump(serialize_state, f)

        logger.info(f"Wrote NeonHandler state to {output_file}")


class CLIUtilities:
    """Main purpose of this class is to avoid accidental invocations of internal methods by InternalCLI.run"""

    @staticmethod
    def parse_and_validate_experiment_spec(encoded_spec: str) -> ExperimentSpec:
        experiment_spec = ExperimentSpec.parse_serialized(json.loads(encoded_spec))
        experiment_spec.validate_against(CLIUtilities.load_project_spec())
        return experiment_spec

    @staticmethod
    def get_and_validate_experiment_spec_from_backend(project_id: str, experiment_id: str, access_token: str) -> tuple[
        ExperimentSpec, str]:
        logger.debug("Fetching (and validating) experiment spec...")
        response = requests.get(f"{backend_endpoint_prefix}/project/{project_id}/experiments/{experiment_id}",
                                headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        response_json = response.json()
        experiment = ExperimentSpec.parse_serialized(response_json['spec'])
        experiment.validate_against(CLIUtilities.load_project_spec())
        logger.debug(" ... done")
        return experiment, response_json['mpspdz_program_hash']

    @staticmethod
    def get_experiment_spec_from_backend(project_id: str, experiment_id: str, access_token: str) -> tuple[
        ExperimentSpec, str]:
        logger.debug("Fetching experiment spec")
        response = requests.get(f"{backend_endpoint_prefix}/project/{project_id}/experiments/{experiment_id}",
                                headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 200
        response_json = response.json()
        experiment = ExperimentSpec.parse_serialized(response_json['spec'])
        logger.debug(" ... done")
        return experiment, response_json['mpspdz_program_hash']

    @staticmethod
    def load_project_spec() -> ProjectSpec:
        with open(os.getenv("PROJECT_SPEC_PATH", "neonik-project.yml"), 'r') as f:
            return ProjectSpec.parse_serialized(yaml.safe_load(f))

    @staticmethod
    def setup_neon(experiment: ExperimentSpec) -> tuple[NeonHandler, AdditionalRunParameters]:
        operation_mode = OperationMode.from_name(os.getenv('NEON_OPERATION_MODE', 'LOCAL_VIRTUAL'))
        config = NeonConfig.from_environment_vars()
        neon = NeonHandler(operation_mode, config)
        additional_parameters = neonik_setup_experiment(neon, experiment)
        logger.critical(f"Checking the substitutions: {neon.find_and_replace}")
        return neon, additional_parameters

    @staticmethod
    def untrusted_setup_neon(neonhandler_state: Dict[str, any]) -> tuple[NeonHandler, str, AdditionalRunParameters]:
        operation_mode = OperationMode.from_name(os.getenv('NEON_OPERATION_MODE', 'LOCAL_VIRTUAL'))
        config = NeonConfig.from_environment_vars()
        neon, program_hash = NeonHandler.deserialize_neon_handler_state(neonhandler_state, operation_mode, config)
        additional_parameters = AdditionalRunParameters()
        return neon, program_hash, additional_parameters

    @staticmethod
    def ensure_additional_parameters_are_supported_for_untrusted_runs(additional_parameters: AdditionalRunParameters) -> None:
         if additional_parameters.post_launch_hook is not None or \
            additional_parameters.post_process_report_hook is not None or \
            additional_parameters.custom_metadata is not None:
            raise ValueError("Untrusted experiments do not support additional arguments.")
         

    @staticmethod
    def send_error(project_id: str, experiment_id: str, access_token: str, step: str,
                   exc: BaseException | None = None) -> None:
        logger.debug("Sending error...")
        if exc is None:
            exc = sys.exception()

        message = "\n".join(traceback.format_exception(exc))

        requests.post(f"{backend_endpoint_prefix}/project/{project_id}/experiments/{experiment_id}/errors",
                      json={
                          "step": step,
                          "message": message
                      },
                      headers={"Authorization": f"Bearer {access_token}"})
        logger.debug(" ... done")

    @staticmethod
    def send_with_retry(
            request_func,
            url,
            *,
            file_name=None,
            file_path=None,
            json_data=None,
            max_attempts=5,
            base_backoff=3,
            max_backoff=30,
            headers=None,
            timeout=(5, 30)
    ):
        """
        Performs repeated HTTP requests with exponential backoff.
        Supports file uploads (via files=) or JSON payloads (via json=)
        """
        response = None

        for attempt in range(1, max_attempts + 1):
            try:
                if file_path:
                    with open(file_path, "rb") as f:
                        response = request_func(
                            url,
                            files={file_name: f},
                            timeout=timeout,
                            headers=headers
                        )
                else:
                    response = request_func(
                        url,
                        json=json_data,
                        headers=headers,
                        timeout=timeout
                    )

                if response.status_code == 200:
                    return response

                delay = min(max_backoff, base_backoff ** attempt)
                logger.warning(
                    f"Request failed (HTTP {response.status_code}). "
                    f"Attempt {attempt}/{max_attempts} - retrying in {delay:.1f}s ..."
                )
                if attempt < max_attempts:
                    time.sleep(delay)

            except (requests.ConnectionError, requests.Timeout) as exc:
                delay = min(max_backoff, base_backoff ** attempt)
                logger.warning(
                    f"Request attempt {attempt}/{max_attempts} failed "
                    f"({type(exc).__name__}: {exc}). Retrying in {delay:.1f}s ..."
                )
                if attempt < max_attempts:
                    time.sleep(delay)

        raise RuntimeError(f"Failed to complete request after {max_attempts} attempts.")

    @staticmethod
    def neon_handler_state_server(access_token: str):
        class NeonHandlerState(BaseModel):
            program: str
            pre_substitution_hash: str
            program_hash: str
            timer_names: Dict[int, str]
            protocol: str
            n_parties: int
            party_inputs: List[str]
            secrets: List[int]
            find_and_replace: Dict[str, str]
            compiled_programs_path: str

            delay: str | None
            incoming_bandwidth: str | None
            outgoing_bandwidth: str | None

            batch_size: int | None
            bucket_size: int | None
            bits_from_squares: bool | None
            prime: int | None

            model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')

        app = FastAPI()

        @app.get("/ready")
        async def is_ready():
            return { "status": "ready" }

        @app.get("/get")
        async def get_neon_handler_state(projectID: str, experimentID: str) -> NeonHandlerState:
            try:
                experiment, _ = CLIUtilities.get_and_validate_experiment_spec_from_backend(projectID, experimentID,
                                                                                           access_token)
                neonhandler, _ = CLIUtilities.setup_neon(experiment)
                serialized_state = neonhandler.serialize_neon_handler_state()
                # Potential to do is to raise error if the Pydantic-Version of the serialized state does not match the original
                # version produced by the NEON-handler. This would save hours of pointless debugging.
                # However, the "extra=allow" in the NeonHandlerState's config should just pass through any extra args :)
                if serialized_state is None:
                    raise HTTPException(status_code=404, detail="No NeonHandler state available yet.")
                return NeonHandlerState(**serialized_state)
            except Exception as e:
                tb = traceback.format_exc()
                print(f"Error while getting NeonHandler state:\n{tb}")
                return JSONResponse(
                    status_code=500,
                    content={"error": str(e), "traceback": tb}
                )

        return app

def main():
    # For UV script
    InternalCLI.run()

if __name__ == '__main__':
    main()
