import json
import os
import shutil
import subprocess
import time

from neonik.additional_run_parameters import AdditionalRunParameters
import numpy as np
from neonik.neon.neonhandler import NeonHandler
from neonik.neon.computationreport import ComputationReport
from neonik.spec import ExperimentSpec

from utils import Event, read_event_logs


def setup_experiment(neon: NeonHandler, experiment: ExperimentSpec) -> AdditionalRunParameters | None:
    match experiment.component_identifier:
        case "split-miner":
            return setup_split_miner(neon, experiment)
        case _:
            raise ValueError(f"Unkown component: {experiment.component_identifier}")


def setup_split_miner(neon: NeonHandler, experiment: ExperimentSpec) -> AdditionalRunParameters | None:
    props = experiment.component_properties
    n_parties = experiment.mpspdz_parties

    event_log: str = props['event-log']
    timestamp_bit_size = int(props['timestamp-bit-size'])
    case_id_bit_size = int(props['case-id-bit-size'])
    repetitions = int(props['repetitions'])
    epsilon = float(props['epsilon'])
    eta = float(props['eta'])
    use_split = props['use-split']
    validate_inputs = props['validate-inputs']
    fixed_event_order = props['fixed-event-order'] == "True"

    # The DFG program relies on split/edabits, so it must not fix a prime field.
    neon.config.prime = None  # type: ignore
    neon.set_program("split_miner")

    neon.set_substitution("NEON_USE_SPLIT", use_split)
    neon.set_substitution("NEON_VALIDATE_INPUTS", validate_inputs)
    neon.set_substitution("NEON_CASE_ID_BITSIZE", case_id_bit_size)
    neon.set_substitution("NEON_TIMESTAMP_BITSIZE", timestamp_bit_size)
    neon.set_substitution("NEON_REPETITIONS", repetitions)
    neon.set_substitution("NEON_EPSILON", str(epsilon))
    neon.set_substitution("NEON_ETA", str(eta))

    ensure_event_log_is_present(event_log, n_parties)
    try:
        reader_output = read_event_logs(
            f'data/{event_log}/{event_log}',
            n_parties,
            timestamp_bit_size,
            case_id_bit_size,
            False,
        )
    except Exception as e:
        raise ValueError(
            f"Failed to read the partial logs of '{event_log}' from "
            f"data/{event_log}; they are staged there from "
            f"experiment-inputs/partial-logs."
        ) from e
    encoded_event_logs: list[list[Event]] = reader_output[0]
    num_of_activities: int = reader_output[1]
    activities: list[str] = reader_output[2]

    print_activities_by_party(encoded_event_logs, activities)

    if fixed_event_order:
        delta = 0
        for encoded_event_log in encoded_event_logs:
            for i in range(len(encoded_event_log)):
                cid, act, time_ = encoded_event_log[i]
                encoded_event_log[i] = (cid, act, time_ + delta)
                delta += 1

    neon.set_substitution("NEON_NUMBER_OF_EVENT_TYPES", str(num_of_activities))
    neon.set_substitution("NEON_NUMBER_OF_ORGANIZATIONS", str(n_parties))
    # The substitution is spliced into a single-quoted Python string literal in the
    # .mpc program, so backslashes and apostrophes must survive Python's own literal
    # parsing (some event logs contain names like "d'r" or "'s morgens").
    activities_json = json.dumps(activities)
    neon.set_substitution(
        "NEON_ACTIVITIES",
        activities_json.replace("\\", "\\\\").replace("'", "\\'")
    )

    # Reference (plaintext) results, printed to sanity-check the SMPC output.
    compute_dfg_native(encoded_event_logs, num_of_activities=num_of_activities + 1)
    compute_short_loops_native(encoded_event_logs, num_of_activities=num_of_activities)

    neon.set_substitution(
        "NEON_NUMBER_OF_EVENTS",
        str([len(encoded_event_logs[i]) for i in range(n_parties)])
    )
    neon.set_substitution(
        "NEON_SUM_OF_EVENTS",
        str(sum(len(encoded_event_logs[i]) for i in range(n_parties)))
    )

    print(f"Total event: {sum(len(encoded_event_logs[i]) for i in range(n_parties))}")

    for i, encoded_event_log in enumerate(encoded_event_logs):
        neon.set_input(i, encode_event_log_as_input(encoded_event_log))

    def launch_socket_miner_client() -> None:
        print("Starting socket miner client.")
        current_path = os.path.split(os.path.abspath(__file__))[0]
        split_miner_client = os.path.join(current_path, "split_miner_socket.py")

        command = ["python3", split_miner_client]
        proc = subprocess.Popen(command, stdout=None, stderr=None)
        proc.wait()

    def add_model_output_to_custom_data(report: ComputationReport) -> ComputationReport:
        def read_model_output(filename: str) -> str | None:
            try:
                with open(os.path.join("model_output", "bpmn", filename)) as f:
                    return f.read()
            except:
                return None

        return report.with_custom_metadata({
            "model-output": {
                "bpmn-with-lanes.bpmn": read_model_output("file.bpmn"),
                "bpmn-without-lanes.bpmn": read_model_output("file-without-lanes.bpmn"),
                "file.svg": read_model_output("file.svg")
            }
        })

    return AdditionalRunParameters(post_launch_hook=launch_socket_miner_client,
                                   post_process_report_hook=add_model_output_to_custom_data)


def print_activities_by_party(encoded_event_logs: list[list[Event]], activities: list[str]) -> None:
    """Print which activity ids/names occur in which party's log (debugging
    activity_to_organization_binning)."""
    print("Activities by party:")
    for party_id, encoded_event_log in enumerate(encoded_event_logs):
        counts: dict[int, int] = {}
        for _, act, _ in encoded_event_log:
            counts[act] = counts.get(act, 0) + 1
        print(f"  Party {party_id} ({len(encoded_event_log)} events, {len(counts)} activities):")
        for act in sorted(counts):
            name = activities[act] if act < len(activities) else "<unknown>"
            print(f"    {act:>4} {name!r}: {counts[act]}")

    activity_to_parties: dict[int, list[int]] = {}
    for party_id, encoded_event_log in enumerate(encoded_event_logs):
        for _, act, _ in encoded_event_log:
            parties = activity_to_parties.setdefault(act, [])
            if party_id not in parties:
                parties.append(party_id)
    print("Parties by activity:")
    for act in sorted(activity_to_parties):
        name = activities[act] if act < len(activities) else "<unknown>"
        print(f"  {act:>4} {name!r}: {activity_to_parties[act]}")


# code/federated/<this file> -> the root of the artifact
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PARTIAL_LOGS = os.path.join(_ROOT, "experiment-inputs", "partial-logs")


def ensure_event_log_is_present(log_name: str, n_parties: int) -> None:
    """Stage one log's partial logs under data/, where the reader expects them.

    The partial logs ship with this repository, so this is a copy and not a
    download; a run needs no network and no credentials.
    """
    for i in range(n_parties):
        party_log_filename = os.path.join("data", log_name, f"{log_name}_{n_parties}_{i}.xes.gz")
        if os.path.isfile(party_log_filename):
            print(f"{party_log_filename} is present.")
            continue

        bundled = os.path.join(PARTIAL_LOGS, log_name,
                               f"{log_name}_{n_parties}_{i}.xes.gz")
        if not os.path.isfile(bundled):
            raise FileNotFoundError(
                f"No partial log {os.path.basename(bundled)} for '{log_name}'. "
                f"The splits that ship with this repository are the folders of "
                f"{PARTIAL_LOGS}; experiment-inputs/README.md describes how to "
                f"produce further ones with split_logs.py."
            )

        print(f"Staging {log_name}_{n_parties}_{i}.xes.gz from experiment-inputs...")
        os.makedirs(os.path.join("data", log_name), exist_ok=True)
        shutil.copyfile(bundled, party_log_filename)


def encode_event_log_as_input(input_event_log: list[Event]) -> str:
    """Encode one party's log as the flat (case id, activity, timestamp) input vector.

    The organization id is deliberately not part of this: the .mpc program fills it
    in from the submitting player's index, so that a malicious party cannot claim
    its events belong to a different organization.
    """
    for event in input_event_log:
        if event[0] > 2 ** 63:
            raise ValueError()
        if event[1] > 2 ** 63:
            raise ValueError()
        if event[2] > 2 ** 63:
            raise ValueError(f"{event[2].bit_length()} {event[2]=}")
    return " ".join(
        " ".join([str(event[0]), str(event[1]), str(event[2])])
        for event in input_event_log
    )


def compute_short_loops_native(encoded_event_logs: list[list[Event]], num_of_activities: int) -> None:
    event_log: list[Event] = []
    for encoded_event_log in encoded_event_logs:
        event_log.extend(encoded_event_log)

    result = np.zeros((num_of_activities, num_of_activities), dtype=int)

    sorted_event_log = sorted(event_log, key=lambda x: (x[0], x[2]))

    for (cid1, act1, _), (cid2, act2, _), (cid3, act3, _) in zip(
        sorted_event_log[:-2], sorted_event_log[1:-1], sorted_event_log[2:]
    ):
        if cid1 == cid3 and act1 == act3:
            result[act1][act2] += 1

    print(result)


def compute_dfg_native(encoded_event_logs: list[list[Event]], num_of_activities: int) -> None:
    event_log: list[Event] = []
    for encoded_event_log in encoded_event_logs:
        event_log.extend(encoded_event_log)

    result = np.zeros((num_of_activities, num_of_activities), dtype=int)

    sorted_event_log = sorted(event_log, key=lambda x: (x[0], x[2]))

    result[num_of_activities - 1][sorted_event_log[0][1]] = 1
    result[sorted_event_log[len(sorted_event_log) - 1][1]][num_of_activities - 1] = 1

    for (cid1, act1, _), (cid2, act2, _) in zip(sorted_event_log[:-1], sorted_event_log[1:]):
        if cid1 == cid2:
            result[act1][act2] += 1
        else:
            result[num_of_activities - 1][act2] += 1
            result[act1][num_of_activities - 1] += 1

    print(result)
