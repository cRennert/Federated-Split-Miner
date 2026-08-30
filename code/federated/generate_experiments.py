#! .venv/bin/python3

import argparse
import itertools
import os
import traceback
from typing import Any, Callable

import yaml

from neonik.spec import ExperimentSpec, ProjectSpec

unrestricted_network = {
    "delay": None,
    "bandwidth-in": None,
    "bandwidth-out": None
}
lan_network = {
    "delay": "1ms",
    "bandwidth-in": "1gbit",
    "bandwidth-out": "1gbit"
}
wan_network = {
    "delay": "10ms",
    "bandwidth-in": "100mbit",
    "bandwidth-out": "100mbit"
}
all_networks = [
    # unrestricted_network,
    lan_network,
    # wan_network
]

all_primitives = [
    "replicated-ring",
    "sy-rep-ring"
]


all_event_logs = [
    #  "sample_2",
    # "sample_10",
    # "sample_50",
    # "sample_100",
    "Hospital_log",  # BPIC 12
    "BPI_Challenge_2012",  # Loan applications
    "BPI_Challenge_2013_open_problems",
    "BPI_Challenge_2013_closed_problems",
    "BPI_Challenge_2013_incidents",
    "BPIC17_Offer_log",
    "BPI_Challenge_2018",
    "BPI_Challenge_2019",  # Purchase order handling
    # "Sepsis_Cases",
    "sepsis_by_department",
    "road_traffic_fine_management",
]

malicious_security_blocklist = [
    "Hospital_log",
    "BPI_Challenge_2018",
    "BPI_Challenge_2019"
]


def generate_split_miner_experiments(
        project_spec: ProjectSpec,
        outfile: str = os.path.join("experiment-specs", "split-miner.yml"),
        event_logs: list[str] | None = None) -> None:
    number_of_executions = 1

    if event_logs is None:
        event_logs = all_event_logs

    target_experiment_groups = [
        {
            "component": "split-miner",
            "network": {"$values": all_networks},
            "mp-spdz": {
                "protocol": "replicated-ring",
                "n-parties": 3,
                "batch-size": 1_000_000,
                "bits-from-squares": False
            },
            "component-properties": {
                "event-log": {"$values": event_logs},
                "timestamp-bit-size": 63,
                "case-id-bit-size": 63,
                "repetitions": 1,
                "epsilon": 0.1,
                "eta": 0.4,
                "use-split": "True",
                # replicated-ring is semi-honest, so the inputs are trusted.
                "validate-inputs": "False",
                "fixed-event-order": "False"
            },
            "number-of-executions": number_of_executions,
            "trusted": True
        },
        {
            "component": "split-miner",
            "network": {"$values": all_networks},
            "mp-spdz": {
                "protocol": "sy-rep-ring",
                "n-parties": 3,
                "batch-size": 1_000_000,
                "bits-from-squares": False
            },
            "component-properties": {
                "event-log": {"$values": event_logs},
                "timestamp-bit-size": 63,
                "case-id-bit-size": 63,
                "repetitions": 1,
                "epsilon": 0.1,
                "eta": 0.4,
                "use-split": "False",
                # Malicious security: the ABB does not constrain what the
                # clients input, so the protocol has to check it itself.
                "validate-inputs": "True",
                "fixed-event-order": "False"
            },
            "number-of-executions": number_of_executions,
            "trusted": True
        }
    ]

    extra_filters = [
        lambda spec: not (spec.mpspdz_protocol.executable.startswith(
            "sy-") and spec.component_properties["event-log"] in malicious_security_blocklist)
    ]

    experiments = generate_experiments(target_experiment_groups, project_spec, additional_filters=extra_filters)
    with open(outfile, "w") as f:
        yaml.dump([spec.serialize() for spec in experiments], f)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate experiment specifications.")
    parser.add_argument(
        "-e", "--event-logs", "--just",
        nargs="+",
        choices=all_event_logs,
        metavar="EVENT_LOG",
        default=all_event_logs,
        help="Restrict generation to these event logs. Defaults to all active event logs: "
             + ", ".join(all_event_logs))
    return parser.parse_args()


def run(event_logs: list[str] | None = None):
    project_specification_file = "neonik-project.yml"
    try:
        with open(project_specification_file, 'r') as f:
            project_spec = ProjectSpec.parse_serialized(yaml.safe_load(f))
    except:
        print("Failed to parse project spec.")
        traceback.print_exc()
        return

    os.makedirs("experiment-specs", exist_ok=True)
    generate_split_miner_experiments(project_spec, event_logs=event_logs)


# region Helper functions
def identify_changing_properties(group: dict[str, Any], prefix="") -> list[tuple[str, list[Any]]]:
    result = []
    for (key, val) in group.items():
        if isinstance(val, dict):
            if "$values" in val:
                result.append((prefix + key, val["$values"]))
            else:
                result.extend(identify_changing_properties(val, prefix + key + "."))
    return result


def update_key(d: dict[str, Any], key: str | list[str], value: Any) -> None:
    if isinstance(key, str):
        key = key.split(".")
    if len(key) == 1:
        d[key[0]] = value
    else:
        update_key(d[key[0]], key[1:], value)


def generate_experiments(experiment_groups: list[dict[str, Any]], project_spec: ProjectSpec,
                         additional_filters: list[Callable[[ExperimentSpec], bool]] | None = None) -> list[ExperimentSpec]:
    if not additional_filters:
        additional_filters = []

    final_experiments = []

    for group in experiment_groups:
        changing_properties = identify_changing_properties(group)
        # print("Group has changing properties:")
        # for (key, values) in changing_properties:
        #     print(f"{key}: {values}")

        prepared_keys = [key.split(".") for (key, _) in changing_properties]
        prepared_values = [values for (_, values) in changing_properties]

        raw_experiment_spec = group.copy()
        for values_to_apply in itertools.product(*prepared_values):
            for (key, val) in zip(prepared_keys, values_to_apply):
                update_key(raw_experiment_spec, key, val)

            # pprint.pprint(raw_experiment_spec)
            try:
                spec = ExperimentSpec.parse_serialized(raw_experiment_spec)
            except:
                print(f"Spec {values_to_apply} could not be parsed.")
                traceback.print_exc()
                continue

            try:
                spec.validate_against(project_spec)
            except:
                print(f"Spec {values_to_apply} did not pass validation.")
                traceback.print_exc()
                continue

            failed_additional_filters: bool = False
            for additional_filter in additional_filters:
                if not additional_filter(spec):
                    failed_additional_filters = True
                    break
            if failed_additional_filters:
                continue

            final_experiments.append(spec)
    print(f"Generated {len(final_experiments)} experiments.")
    return final_experiments

# endregion


if __name__ == "__main__":
    run(parse_args().event_logs)
