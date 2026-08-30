import os
import sys
import importlib

from neonik.additional_run_parameters import AdditionalRunParameters
from neonik.neon.computationreport import ComputationReport
from neonik.neon.neonhandler import NeonHandler
from neonik.spec import ExperimentSpec


def neonik_setup_experiment(neon: NeonHandler, experiment: ExperimentSpec, user_module: str = "main") -> AdditionalRunParameters:
    def set_enforced_properties():
        neon.set_network(experiment.network)
        neon.set_protocol(experiment.mpspdz_protocol)
        neon.set_number_of_parties(experiment.mpspdz_parties)
        neon.set_batch_size(experiment.mpspdz_batch_size)
        neon.set_bits_from_squares(experiment.mpspdz_bits_from_squares)

    set_enforced_properties()  # We set once such that the user can access them via the neon handler

    # Ensure that the 
    try:
        module = importlib.import_module(user_module)
    except:
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        module = importlib.import_module(user_module)

    # Call the user's setup_experiment function
    additional_parameters = module.setup_experiment(neon, experiment)
    set_enforced_properties()  # We set again for the case the user changed the parameters.

    if additional_parameters is None:
        additional_parameters = AdditionalRunParameters()
    return additional_parameters


def run_smpc_with_additional_parameters(neon: NeonHandler, additional_parameters: AdditionalRunParameters,
                                        skip_compile: bool = False) -> ComputationReport:
    report = neon.smpc(
            custom_metadata=additional_parameters.custom_metadata,
            post_launch_hook=additional_parameters.post_launch_hook,
            post_launch_hook_arguments=additional_parameters.post_launch_hook_args,
            skip_compile=skip_compile)
        
    if additional_parameters.post_process_report_hook:
        report = additional_parameters.post_process_report_hook(report)
    return report


def execute_experiment(neon: NeonHandler, experiment: ExperimentSpec, user_module: str = "main",
                       skip_compile: bool = False) -> ComputationReport:
    additional_parameters = neonik_setup_experiment(neon, experiment, user_module)
    return run_smpc_with_additional_parameters(neon, additional_parameters, skip_compile=skip_compile)

    