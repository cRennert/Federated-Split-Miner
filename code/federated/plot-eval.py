#! .venv/bin/python3
"""Evaluation plotting/tabulation for the federated split-miner protocol.

This script is the analogue of the ``plot_declare`` evaluation script from the
DECLARE project: it loads the computation reports produced by
``run-all-experiments experiment-specs/split-miner.yml`` and emits a LaTeX table
(plus a CSV) that breaks the protocol's runtime down into its individual steps.

The step timers are identified by the ``Timer`` context managers scattered
across ``Programs/Dependencies/*`` and are printed as a name -> id mapping at the
end of every run (``Timer.pretty_print_timer_ids()``). That mapping is mirrored
in ``TIMERS`` below. Reports themselves only store timer *ids*, so we look the
timers up by id.

To adjust which steps end up in the table, edit ``TABLE_TIMERS`` (the DataFrame
built by ``build_df_prototype`` contains a column for *every* timer in
``TIMERS``, so you can move any of them into the table without touching the
loading code).

Several timers can be merged into one combined step (e.g. "total input" =
"input reading" + "input-validation") by adding an entry to ``TIMER_GROUPS``.
Groups behave exactly like real timers: they get the same runtime/rounds/data
columns and can be listed in ``TABLE_TIMERS``.
"""

import math
import os.path
import shutil
import traceback
from typing import Callable, Any

import humanize
import numpy as np
from matplotlib import pyplot as plt
from pandas import DataFrame
import seaborn as sns
from tqdm import tqdm

from neonik.neon.computationreport import ComputationReport, ComputationReportList

primitive_display_names = {
    'replicated-ring': 'semi-honest replicated',
    'replicated-field': 'semi-honest replicated (\\mathbb{F}_p)',
    'ps-rep-ring': 'Post-Sacrifice replicated',
    'ps-rep-field': 'malicious (\\mathbb{F}_p)',
    'malicious-rep-ring': 'slow malicious-rep-ring',
    'sy-rep-ring': 'SPDZ-wise replicated',
    'mascot': "MASCOT",
    'spdz2k': "SPD\\mathbb{Z}_{2^k}",
    'semi2k': "semi2k",
    'hemi': "hemi",
    'semi': 'semi'
}

# region Timers
# Timer id -> human-readable label, mirroring the name->id mapping printed by
# `Timer.pretty_print_timer_ids()` at the end of a run. Every timer listed here
# becomes a set of columns in the DataFrame built by `build_df_prototype`
# ("<label> [s]", "<label> rounds", "<label> data [MB]"). Labels are chosen to be
# unambiguous on their own; nested timers are prefixed with their parent step.
#
# NOTE: A report only contains the timers that were actually executed during that
# run, so timers missing from a report show up as NaN (never as a crash).
TIMERS: dict[int, str] = {
    # --- top level (Programs/split_miner.mpc) ---
    11:        "eval (total)",
    111:       "input reading",
    113:       "main",
    # --- federated-pm / log-merge (Programs/Dependencies/log_merge.py) ---
    14011:     "log-merge",
    140111:    "log-merge: prep",
    140112:    "log-merge: caseid-encryption",
    140113:    "log-merge: caseid-sort",
    140114:    "log-merge: caseid-boundaries",
    140115:    "log-merge: timestamp-sort",
    # --- federated-pm / split-miner (Programs/Dependencies/split_miner.py) ---
    14031:     "client-init",
    14032:     "smpc-computation",
    14033:     "client-local-computation",
    # smpc-computation stages
    1403201:   "dfg",
    1403202:   "$\\algname{}{SelfLoops}$",  # "self-loops",
    1403203:   "$\\algname{}{ShortLoops}$",  # "short-loops",
    1403204:   "$\\algname{}{Concurrency}$",  # "concurrency",
    1403205:   "$\\algname{}{Prune}$",  # "pruning",
    1403206:   "reformat",
    1403207:   "$\\algname{}{MaxCapacity}$",  # "capacity-edge-filter",
    1403208:   "$\\algname{}{EtaFilter}$",  # "eta-filter",
    1403209:   "filter-combine",
    1403210:   "$\\algname{}{Binning}$",  # "activity-binning",
    1403211:   "$\\algname{}{Sanitize}$",
    1403212:   "output-to-client",
    1403213:   "input-validation",
    # dfg internals (Programs/Dependencies/dfg.py)
    # NOTE: `split_miner.py` uses the *fully oblivious* DFG builder, whose four
    # `Timer`s (log-merge, demux, matmul-prep, matmul; see `dfg.py`) get ids
    # ...11 to ...14 in that order. Do not confuse ...13 with the `Matrix-Mul`
    # timer of the case-length-leaking builder, which would be ...13 as well.
    14032011: "$\\algname{}{EventEncoding}$",  # "dfg: log-merge",
    14032012:  "dfg: demux",
    14032013:  "dfg: matmul-prep",
    14032014:  "dfg: matmul",
    # capacity-edge-filter internals
    14032071:  "capacity-edge-filter: forward",
    14032072:  "capacity-edge-filter: backwards",
    14032073:  "capacity-edge-filter: demux",
    # activity-binning internals
    14032101:  "activity-binning: demux-orgs",
    14032102:  "activity-binning: matmul",
    14032103:  "activity-binning: normalize",
}

# Combined timers ("timer groups"): a synthetic timer id -> (label, members).
# A group's runtime/rounds/data are the sums of its members, so it gets exactly
# the same three columns as a real timer ("<label> [s]", "<label> rounds",
# "<label> data [MB]") and can be used in `TABLE_TIMERS` interchangeably.
#
# Members may be real timer ids or other group ids (groups nest). Give groups
# negative ids so they can never collide with real MP-SPDZ timer ids. As with
# real timers, only sum *disjoint* members -- nesting timers double-counts. If
# any member is missing from a report, the group is NaN for that report.
TIMER_GROUPS: dict[int, tuple[str, list[int]]] = {
    -1: ("Input", [111, 1403213, 14031]),
    -2: ("$\\algname{}{DFG}$", [14032012, 14032013, 14032014]),
    -3: ("$\\algname{}{Filter}$", [1403207, 1403208, 1403209]),
    -4: ("Building model", [1403212, 14033]),
    -5: ("Total [s]", [111, 113])
}

# The single timer that represents the whole protocol run (one repetition body).
# `[eval] [main]` wraps the actual `discover_bpmn` call, excluding input reading.
TOTAL_TIMER: int = 113

# All timers that get columns: real timers plus the combined ones.
ALL_TIMERS: dict[int, str] = {**TIMERS, **{timer: label for timer, (label, _) in TIMER_GROUPS.items()}}

# The subset of `ALL_TIMERS` that ends up as columns in the LaTeX runtime-by-stage
# table (in this order). Edit this to taste -- every timer (or timer group) here
# also exists as a column in the DataFrame, so no other change is required.
#
# Beware of overlap when choosing: timers nest, so summing overlapping timers
# double-counts. The default below is the disjoint set of `smpc-computation`
# stages (siblings that partition it). Note `dfg` runs the log-merge internally
# (timer 14032011, "dfg: log-merge"), which is where most of the merge cost
# lives -- so do not also add the standalone `log-merge` (14011) unless you mean
# to double-count it.
TABLE_TIMERS: list[int] = [
    -1,
    # 1403213,    # input-validation (0 unless "validate-inputs" is set)
    14032011,  # Log merge
    -2,    # Prot dfg
    1403202,  # self loops
    1403203,    # short-loops
    1403204,    # concurrency
    1403205,    # pruning
    # 1403207,    # capacity-edge-filter
    # 1403208,    # eta-filter
    -3,  # Pi_Filter
    1403211,  # sanitize
    1403210,    # activity-binning,
    # 14033,      # client local computation
    -4,
    -5
]


def resolve_timer_members(timer: int, _seen: frozenset[int] = frozenset()) -> list[int]:
    """Flattens a timer id into the real timer ids it consists of.

    A plain timer resolves to itself; a group in ``TIMER_GROUPS`` resolves to the
    (recursively flattened) ids of its members.
    """
    if timer not in TIMER_GROUPS:
        return [timer]
    if timer in _seen:
        raise ValueError(f"Cyclic timer group definition involving group {timer}.")
    _seen = _seen | {timer}
    members: list[int] = []
    for member in TIMER_GROUPS[timer][1]:
        members.extend(resolve_timer_members(member, _seen))
    return members


def _sum_over_members(timer: int, value_of: Callable[[int], float]) -> float:
    """Sums ``value_of`` over the timer's members; NaN if any member is missing."""
    total = 0.0
    for member in resolve_timer_members(timer):
        value = value_of(member)
        if value is None or math.isnan(value):
            return math.nan
        total += value
    return total


def safe_timer_average(report: ComputationReport, timer: int) -> float:
    """Average of a timer across the report's clients, or NaN if it is absent."""
    def single(t: int) -> float:
        if any(t not in c.timers for c in report.client_reports):
            return math.nan
        return report.get_timer_average(t)

    return _sum_over_members(timer, single)


def safe_timer_rounds(report: ComputationReport, timer: int) -> float:
    """Communication rounds of a timer (from client 0), or NaN if absent."""
    def single(t: int) -> float:
        rounds = report.client_reports[0].timers_communication_rounds
        if rounds is None or t not in rounds:
            return math.nan
        return rounds[t]

    return _sum_over_members(timer, single)


def safe_timer_data_mb(report: ComputationReport, timer: int) -> float:
    """Total data sent (MB) for a timer, summed over clients, or NaN if absent."""
    def single(t: int) -> float:
        total = 0.0
        for client in report.client_reports:
            data = client.timers_data_sent
            if data is None or t not in data:
                return math.nan
            # Values look like "53.675 MB"; drop the trailing " MB".
            total += float(data[t][:-3])
        return total

    return _sum_over_members(timer, single)
# endregion


event_log_display_names = {
    "sample_2": "Sample 2",
    "sample_10": "Sample 10",
    "sample_50": "Sample 50",
    "sample_100": "Sample 100",

    "Hospital_log": "BPIC 11: Hospital Log",
    "BPI_Challenge_2012": "BPIC 12: Loan applications",
    "BPI_Challenge_2013_open_problems": "BPIC 13: Open Problems",
    "BPI_Challenge_2013_closed_problems": "BPIC 13: Closed Problems",
    "BPI_Challenge_2013_incidents": "BPIC 13: Incidents",
    "BPIC17_Offer_log": "BPIC 17: Offer log",
    "BPI_Challenge_2018": "BPIC 18: Payment process",
    "BPI_Challenge_2019": "BPIC 19: Purchase order handling",

    "Sepsis_Cases": "Sepsis Cases",
    "sepsis_by_department": "Sepsis Cases (by department)",
    "road_traffic_fine_management": "Road Traffic Fines",

}


def plot_split_miner(reports_folder: str, figures_folder: str, tables_folder: str,
                     category_name: str = "split-miner") -> None:
    tables_folder = create_output_folder(tables_folder, category_name)
    commit_versions = determine_commit_versions()

    # The commit the reported measurements start from. It belongs to the history
    # in which the evaluation was developed, so a checkout that does not carry
    # that history -- the published artifact, or the container image, which has
    # no repository at all -- cannot order its reports against it.
    MIN_VERSION = '9d8c40cb64a62ed54f2b05195b1186e5e12b5dc8'

    def check_min_version(report: ComputationReport) -> bool:
        if report.experiment_spec is None:
            return False
        if MIN_VERSION not in commit_versions:
            # Nothing to compare against: take the reports as they are, which is
            # what a run of one's own produces.
            return True
        if report.custom_version is None:
            return False
        if commit_versions[report.custom_version] < commit_versions[MIN_VERSION]:
            return False
        # To require a minimal Git commit version, uncomment (and adjust) the
        # following, using the commit map from `determine_commit_versions()`:
        # commit_versions = determine_commit_versions()
        # if report.custom_version is None:
        #     return False
        # return commit_versions[report.custom_version] >= commit_versions['<commit-hash>']
        return True

    def extra_attributes(report: ComputationReport) -> dict[str, Any]:
        props = report.experiment_spec['component-properties']
        event_log = props['event-log']
        subs = report.substitutions

        timers = {}
        # One set of columns per timer in TIMERS and per group in TIMER_GROUPS.
        for timer, label in ALL_TIMERS.items():
            timers[f"{label} [s]"] = safe_timer_average(report, timer)
            timers[f"{label} rounds"] = safe_timer_rounds(report, timer)
            timers[f"{label} data [MB]"] = safe_timer_data_mb(report, timer)

        return {
            'int_event_log': event_log,
            'event log': event_log_display_names.get(event_log, event_log),
            'epsilon': float(props['epsilon']),
            'eta': float(props['eta']),
            'number of events': int(subs['NEON_SUM_OF_EVENTS']),
            'number of activities': int(subs['NEON_NUMBER_OF_EVENT_TYPES']),
            **timers
        }

    df_complete = build_df_prototype(os.path.join(reports_folder, category_name),
                                     extra_filter=check_min_version,
                                     extra_attributes=extra_attributes)
    print(f"Loaded {len(df_complete)} report(s).")
    if len(df_complete) == 0:
        print("No reports to tabulate -- run `run-all-experiments experiment-specs/split-miner.yml` first.")
        return
    print(f"Total runtime tabulated: {humanize.precisedelta(df_complete['runtime [s]'].sum())}")

    df_tables = df_complete

    primitive_fileparts = {
        "replicated-field": "sh-field",
        "replicated-ring": "sh-ring",
        "sy-rep-ring": "mal-ring",
    }
    network_fileparts = {
        "0": "unres",
        "1ms": "lan",
        "10ms": "wan",
    }

    for ((primitive, delay), data) in tqdm(df_tables.groupby(["int_primitive", "delay"]),
                                           desc="Writing tables"):
        primitive_part = primitive_fileparts.get(primitive, primitive)
        network_part = network_fileparts.get(delay, delay)

        data.to_csv(os.path.join(tables_folder, f"stages-{primitive_part}-{network_part}.csv"))
        write_runtime_by_stage_table(
            os.path.join(tables_folder, f"runtime-by-stage-{primitive_part}-{network_part}.tex"),
            data,
            primitive=primitive,
            network_part=network_part,
            include_total=False
        )


def write_runtime_by_stage_table(filename: str, data: DataFrame, primitive: str,
                                 network_part: str, include_total: bool) -> None:
    """Writes a LaTeX table: one row per event log, one column per TABLE_TIMERS step."""
    stage_labels = [ALL_TIMERS[timer] for timer in TABLE_TIMERS]
    column_headers = ["Events", "$|A|$"] + [label for label in stage_labels] + (["Total [s]"] if include_total else [])

    table_caption = (f"Runtimes (in seconds) of the split-miner protocol stages using the "
                     f"\\texttt{{{primitive}}} primitive in the {network_part.upper()} setting.")
    table_label = f"split-miner:tab:runtime-by-stage-{tex_escape(primitive)}-{network_part}"
    column_spec = "l" + "r" * (len(column_headers))

    present_event_logs = data['int_event_log'].unique()
    event_log_order = event_log_display_names.keys()

    with open(filename, "w") as f:
        f.write("\\begin{table}[ht!]\n")
        f.write(f"    \\caption{{{table_caption}}}\n")
        f.write(f"    \\label{{{table_label}}}\n")
        f.write("    \\centering\n")
        f.write(f"    \\resizebox{{\\linewidth}}{{!}}{{\\begin{{tabular}}{{{column_spec}}}\n")
        f.write("        \\toprule\n")
        f.write("        Event log & " + " & ".join(column_headers) + " \\\\\n")
        f.write("        \\midrule\n")

        for event_log in event_log_order:
            if event_log not in present_event_logs:
                continue

            data_log = data[data['int_event_log'] == event_log]
            if len(data_log) == 0:
                continue

            row = [event_log_display_names.get(event_log, event_log)]
            row.append(latex_format_large_number(int(round(data_log['number of events'].mean()))))
            row.append(latex_format_large_number(int(round(data_log['number of activities'].mean()))))
            for label in stage_labels:
                row.append(format_seconds(data_log[f"{label} [s]"].mean()))
            if include_total:
                row.append(format_seconds(data_log['runtime [s]'].mean()))
            f.write("        " + " & ".join(row) + " \\\\\n")

        f.write("        \\bottomrule\n")
        f.write("    \\end{tabular}}\n")
        f.write("\\end{table}\n")


def run():
    # matplotlib.use("Agg")  # Once was required in a devcontainer.
    plt.rcParams.update({
        'figure.dpi': 200,
        'figure.constrained_layout.use': True,
        'figure.figsize': (6.4 * 1.5, 4.2 * 1.5),
        # No LaTeX is required for the tables; flip on if you add usetex figures.
        'text.usetex': False,
        'font.size': 13
    })

    reports_folder = os.path.join("evaluation-reports")
    figures_folder = "figures"
    tables_folder = "tables"

    plot_split_miner(reports_folder, figures_folder, tables_folder)


# region Helper methods

def export_reports(df: DataFrame, export_folder: str = os.path.join("paper", "main-eval")) -> None:
    os.makedirs(export_folder, exist_ok=True)
    for filename in df['filename'].values:
        report_name = filename.split("/")[-1]
        target_name = os.path.join(export_folder, report_name)
        shutil.copy(filename, target_name)


def create_output_folder(parent: str, category: str) -> str:
    folder = os.path.join(parent, category)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    return folder


def build_df_prototype(input_folder: str,
                       extra_filter: Callable[[ComputationReport], bool] | None = None,
                       extra_attributes: Callable[[ComputationReport], dict[str, str | float | int]] | None = None) -> DataFrame:
    """Loads all computation reports in ``input_folder`` into a DataFrame.

    Besides the general metadata columns, every timer in ``TIMERS`` and every
    combined timer in ``TIMER_GROUPS`` contributes three columns:
    ``"<label> [s]"``, ``"<label> rounds"`` and ``"<label> data [MB]"``. Timers
    that a report does not contain become NaN.
    """
    report_list = ComputationReportList.from_folder(input_folder)
    records: list[dict[str, str | float | int]] = []
    for report in tqdm(report_list, desc="Loading computation reports"):
        if report is None:
            continue
        report: ComputationReport

        # Filter out failed computations, e.g., computations that ran out of RAM.
        if report.client_reports[0].cpu_time is None:
            continue

        if extra_filter is not None and extra_filter(report) is False:
            continue

        network_setting = "unrestricted"
        if report.network.delay is not None:
            network_setting = f"{report.network.delay}, {report.network.incoming_bandwidth} down, {report.network.outgoing_bandwidth} up"
        network_delay = report.network.delay if report.network.delay is not None else "0"
        network_bandwidth = report.network.incoming_bandwidth if report.network.incoming_bandwidth is not None else "None"

        n_repetitions = 1
        for repetitions_name in ["NEON_N_REPETITIONS", "NEON_NUMBER_OF_REPETITIONS", "NEON_REPETITIONS"]:
            if repetitions_name in report.substitutions:
                n_repetitions = int(report.substitutions[repetitions_name])
                break

        primitive = primitive_display_names.get(report.protocol, report.protocol)
        is_semi_honest = report.protocol in {"replicated-ring", "replicated-field"}
        use_split = ('NEON_USE_SPLIT' in report.substitutions) and (report.substitutions['NEON_USE_SPLIT'] == "True")

        total_runtime = safe_timer_average(report, TOTAL_TIMER)
        total_rounds = safe_timer_rounds(report, TOTAL_TIMER)
        total_data = safe_timer_data_mb(report, TOTAL_TIMER)

        row = {
            'primitive': primitive,
            'semi-honest primitive': is_semi_honest,
            'int_primitive': report.protocol,
            'network setting': network_setting,
            'delay': network_delay,
            'bandwidth': network_bandwidth,
            'number of compute parties': len(report.client_reports),
            'batch size': report.batch_size if report.batch_size is not None else 10000,

            'repetitions': n_repetitions,
            'wall-clock runtime [s]': report.get_average_total_runtime(),
            'total cpu time [s]': report.get_average_cpu_time(),
            'use split': use_split,

            # Protocol runtime (the TOTAL_TIMER, i.e. one `main` body).
            'runtime [s]': total_runtime,
            'average runtime [s]': total_runtime / n_repetitions,
            'runtime [min]': total_runtime / 60,
            'communication rounds': total_rounds,
            'data sent [MB]': total_data,
            'max memory usage [bytes]': report.max_memory_usage,
        }

        if extra_attributes is not None:
            try:
                row.update(extra_attributes(report))
            except Exception:
                traceback.print_exc()
                continue
        records.append(row)

    return DataFrame.from_records(records)


def format_seconds(x: float) -> str:
    """Formats a runtime in seconds for a LaTeX table cell ($\\bot$ for missing)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "$\\bot$"
    return latex_format_large_number(f"{x:.2f}")


def tex_escape(text: str) -> str:
    """Escapes the few LaTeX-special characters that appear in our labels."""
    for char in ("\\", "_", "%", "&", "#", "$"):
        text = text.replace(char, "\\" + char)
    return text


def latex_format_large_number(x) -> str:
    """
    Formats a large number into a LaTeX-friendly string that inserts small spaces at appropiate spots.
    Useful for table creation.
    """
    x = str(x).split(".")
    if len(x) == 1:
        pre_comma_part = x[0]
        post_comma_part = ""
    else:
        pre_comma_part = x[0]
        post_comma_part = f".{x[1]}"

    pre_comma_part_parts = []
    for i in range(len(pre_comma_part), 0, -3):
        pre_comma_part_parts.insert(0, pre_comma_part[max(i - 3, 0):i])
    pre_comma_part = "\\,".join(pre_comma_part_parts)
    return pre_comma_part + post_comma_part


def determine_commit_versions() -> dict[str, int]:
    """Map every commit of this checkout to its position in the history.

    Returns an empty map where there is no history to read: outside a Git
    checkout, or without Git installed, as in the container image. The callers
    treat an empty map as "provenance cannot be established here" rather than as
    "every report is too old".
    """
    import subprocess

    try:
        proc = subprocess.run(["git", "--no-pager", "log"],
                              capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {}
    commit_history = []
    for line in proc.stdout.split('\n'):
        if line.startswith("commit"):
            commit_history.append(line.split(' ')[1].strip())

    result = {}
    for i, commit in enumerate(reversed(commit_history)):
        result[commit] = i
    return result


def custom_despine(offset_x_axis: bool = True, offset_y_axis: bool = True,
                   force_x_axis_to_zero: bool = True,
                   force_y_axis_to_zero: bool = True) -> None:
    """
    Makes figures more beautiful by removing unnecessary lines.
    Also ensures that the axes all scale to zero.
    """
    axes = plt.gcf().axes

    for ax_i in axes:
        ax_i.spines["top"].set_visible(False)
        ax_i.spines["right"].set_visible(False)

        if offset_x_axis:
            ax_i.spines["bottom"].set_position(("outward", 5))
        if offset_y_axis:
            ax_i.spines["left"].set_position(("outward", 5))
        if force_x_axis_to_zero:
            _, xmax = ax_i.get_xlim()
            ax_i.set_xlim((0, xmax))
        if force_y_axis_to_zero:
            _, ymax = ax_i.get_ylim()
            ax_i.set_ylim((0, ymax))

        yticks = np.asarray(ax_i.get_yticks())
        if yticks.size:
            firsttick = np.compress(yticks >= min(ax_i.get_ylim()),
                                    yticks)[0]
            lasttick = ax_i.get_ylim()[1]
            ax_i.spines['left'].set_bounds(firsttick, lasttick)
            ax_i.spines['right'].set_bounds(firsttick, lasttick)
            newticks = yticks.compress(yticks <= lasttick)
            newticks = newticks.compress(newticks >= firsttick)
            ax_i.set_yticks(newticks)

        handles, labels = ax_i.get_legend_handles_labels()
        ax_i.legend(handles=handles, labels=labels, frameon=False)


# endregion


if __name__ == '__main__':
    run()
