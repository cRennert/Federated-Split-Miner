"""Discover the flattened BPMN of every evaluation log twice -- once from the
original log and once from the three partial logs of the federated split -- and
compare both against the Split Miner 1.0 model that ships next to them.

The comparison is done on the filtered directly-follows graph rather than on the
BPMN syntax: as argued in Section 4, the FPDFG can be read back off a Split Miner
model by taking, for every pair of tasks, the paths between them whose internal
nodes are all gateways.  Two models that induce the same FPDFG describe the same
control flow regardless of how the gateway trees happen to be nested.
"""
from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reference_split_miner import discover

# code/reference/<this file> -> the root of the artifact
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Discovered models are written next to the ones we ship. Both the partial and
# the original logs come with this artifact; the BPIC 18 log has to be joined
# from its parts first (see experiment-inputs/README.md).
OUT_DIR = os.path.join(_ROOT, "experiment-outputs", "models")
ORIG_DIR = os.path.join(_ROOT, "experiment-inputs", "original-logs")
FED_DIR = os.path.join(_ROOT, "experiment-inputs", "partial-logs")

# eta states how much behavior to filter away, as in the original tool,
# whose default of 0.4 this is
EPSILON, ETA = 0.1, 0.4

# folder in OUT_DIR -> (original log file, folder in binning_output)
LOGS = [
    ("BPI_Challenge_2013_open_problems", "BPI_Challenge_2013_open_problems.xes.gz",
     "BPI_Challenge_2013_open_problems"),
    ("BPI_Challenge_2013_closed_problems", "BPI_Challenge_2013_closed_problems.xes.gz",
     "BPI_Challenge_2013_closed_problems"),
    ("sepsis", "Sepsis Cases.xes.gz", "Sepsis_Cases"),
    ("BPI_Challenge_2013_incidents", "BPI_Challenge_2013_incidents.xes.gz",
     "BPI_Challenge_2013_incidents"),
    ("Hospital_log", "Hospital_log.xes.gz", "Hospital_log"),
    ("BPI_Challenge_2012", "BPI_Challenge_2012.xes.gz", "BPI_Challenge_2012"),
    ("road_traffic_fine_management", "road_traffic_fine_management.xes.gz",
     "road_traffic_fine_management"),
    ("BPIC17_Offer_log", "BPIC17 - Offer log.xes.gz", "BPIC17_Offer_log"),
    ("BPI_Challenge_2019", "BPI_Challenge_2019.xes.gz", "BPI_Challenge_2019"),
    ("BPI_Challenge_2018", "BPI_Challenge_2018.xes.gz", "BPI_Challenge_2018"),
]

BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"


def fpdfg_of_bpmn_file(path):
    """Read a BPMN file and recover the arcs of its filtered DFG."""
    root = ET.parse(path).getroot()
    tag = lambda e: e.tag.rsplit("}", 1)[-1]
    label, kind = {}, {}
    for el in root.iter():
        t = tag(el)
        if t in ("task", "startEvent", "endEvent", "userTask", "serviceTask",
                 "manualTask", "sendTask", "receiveTask", "scriptTask",
                 "businessRuleTask", "callActivity", "subProcess"):
            label[el.get("id")] = el.get("name") or ("__start__" if t == "startEvent"
                                                     else "__end__")
            kind[el.get("id")] = "task"
        elif t.endswith("Gateway"):
            kind[el.get("id")] = "gateway"
    succ = defaultdict(list)
    for el in root.iter():
        if tag(el) == "sequenceFlow":
            succ[el.get("sourceRef")].append(el.get("targetRef"))
    arcs = set()
    for u in [i for i, k in kind.items() if k == "task"]:
        seen, stack = set(), list(succ[u])
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if kind.get(x) == "task":
                arcs.add((label[u], label[x]))
            elif kind.get(x) == "gateway":
                stack.extend(succ[x])
    return arcs, {v for k, v in label.items() if kind[k] == "task"}


def fpdfg_of_artifacts(art):
    return {("__start__" if u == ">" else u, "__end__" if v == "#" else v)
            for (u, v) in art.sanitized}


def run(folder, orig_file, fed_folder):
    out = os.path.join(OUT_DIR, folder)
    results = {}
    for tag_, paths in (
        ("original", [os.path.join(ORIG_DIR, orig_file)]),
        ("federated", [os.path.join(FED_DIR, fed_folder, f"{fed_folder}_3_{i}.xes.gz")
                       for i in range(3)]),
    ):
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            print(f"    {tag_}: missing {missing[0]}")
            continue
        t0 = time.time()
        art = discover(paths, epsilon=EPSILON, eta=ETA)
        dst = os.path.join(out, f"FederatedSplitMiner_{tag_}.bpmn")
        bpmn = art.write_bpmn(dst)
        results[tag_] = art
        print(f"    {tag_:<10} {art.n_events:>9,} events  {art.n_cases:>8,} cases  "
              f"m={len(art.activities):<4} {len(bpmn.get_nodes()):>5} nodes "
              f"{len(bpmn.get_flows()):>5} flows  {time.time()-t0:6.1f}s")
    return results


def compare(folder, results):
    ref_path = os.path.join(OUT_DIR, folder, "SplitMiner1.0.bpmn")
    if not os.path.exists(ref_path) or not results:
        return
    ref_arcs, ref_tasks = fpdfg_of_bpmn_file(ref_path)
    for tag_, art in results.items():
        mine = fpdfg_of_artifacts(art)
        mine_tasks = set(art.activities) | {"__start__", "__end__"}
        common = ref_arcs & mine
        print(f"      vs SplitMiner1.0 [{tag_}]: tasks {len(mine_tasks)} vs "
              f"{len(ref_tasks)}"
              f"{' (same set)' if mine_tasks == ref_tasks else ' (DIFFER)'}"
              f", arcs {len(mine)} vs {len(ref_arcs)}, shared {len(common)}, "
              f"only-mine {len(mine - ref_arcs)}, only-ref {len(ref_arcs - mine)}")
    if "original" in results and "federated" in results:
        a = fpdfg_of_artifacts(results["original"])
        b = fpdfg_of_artifacts(results["federated"])
        same = a == b
        print(f"      federated vs original: "
              f"{'identical' if same else f'differ by {len(a ^ b)} arcs'}")


if __name__ == "__main__":
    only = sys.argv[1:] or None
    for folder, orig_file, fed_folder in LOGS:
        if only and folder not in only:
            continue
        print(f"\n{folder}")
        try:
            res = run(folder, orig_file, fed_folder)
            compare(folder, res)
        except Exception as exc:                       # keep going on the big ones
            print(f"    FAILED: {type(exc).__name__}: {exc}")
