"""Split Miner on full event log without frequency mitigation

Pairing
-------
This script uses ``socket_program`` to setup a connection to an MP-SPDZ run.

Running the code
----------------
This needs to terminals:
(1) runs the MP-SPDZ code that provides data
(2) is the client doing computations (here the Split Miner) on clear event data
"""

from socket_client import SocketClientSession
from pm4py.objects.bpmn.obj import BPMN
from pm4py.objects.bpmn.layout import layouter as bpmn_layouter
from pm4py.objects.bpmn.exporter.variants.etree import get_xml_string
from pm4py.objects.bpmn.exporter.variants import etree as bpmn_etree_exporter
from pm4py.algo.discovery.split_miner.variants.abc import SplitMinerFramework as split_miner
from pm4py.algo.discovery.split_miner.dtypes.loops import LoopInfo
from pm4py.algo.discovery.split_miner.bpmn_init.classic import ConcurrencyResult
from pm4py.algo.discovery.split_miner.filtering.abc import FilterResult
import pm4py
from lxml import etree
import json
import os
import sys
import uuid
from collections import defaultdict, deque
from typing import List

# In the cluster stdout is a pipe, so it is block-buffered and the tail of the
# run is lost whenever the script hangs or is killed -- which is exactly when
# the log is needed. Line buffering costs nothing at this print volume.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


def _color_pools(nodes: list, flows: list, activity_to_pool_lane: dict) -> tuple:
    """Assign every node in a flat pm4py BPMN graph to a pool.

    A task/event is seeded with the pool of the activity it was mined from
    (activity_to_pool_lane maps activity *name* -> (pool, lane); lane is
    unused -- split_miner_on_dfg_with_frequencies never sets one, an org
    either touches an activity or it doesn't). Everything else -- gateways,
    and the synthetic __start__/__end__ events, none of which appear in
    activity_to_pool_lane -- has no pool of its own and instead inherits the
    pool of whichever seed reaches it in the fewest hops, via multi-source
    BFS over the undirected sequence-flow graph. Split Miner's output is
    mostly gateways (long xor/and chains around every choice and loop), so
    a node's immediate neighbours are very often other unresolved gateways
    rather than a task; only looking at immediate neighbours previously
    coloured some gateways essentially at random, producing message flows
    into pools that a path never actually reached.

    Returns (coloring, pools): coloring maps every node to a pool name, and
    pools is the sorted list of distinct pool names actually used.
    """
    name_to_pool = {
        act: (pool if pool is not None else "ProcessPool")
        for act, (pool, _lane) in activity_to_pool_lane.items()
    }

    adjacency: dict = defaultdict(set)
    for f in flows:
        adjacency[f.get_source()].add(f.get_target())
        adjacency[f.get_target()].add(f.get_source())

    coloring: dict = {}
    # Sorted by id for a deterministic BFS seed order (ties go to whichever
    # source's wave arrives first), rather than dict/set iteration order.
    queue = deque()
    for n in sorted(nodes, key=lambda n: n.get_id()):
        pool = name_to_pool.get(n.get_name())
        if pool:
            coloring[n] = pool
            queue.append(n)
    visited = set(queue)
    while queue:
        current = queue.popleft()
        for neighbour in adjacency.get(current, ()):
            if neighbour in visited:
                continue
            visited.add(neighbour)
            coloring[neighbour] = coloring[current]
            queue.append(neighbour)

    pools = sorted(set(coloring.values())) or ["ProcessPool"]
    # A node whose whole connected component has no named activity at all
    # (e.g. a fully isolated fragment) still needs some pool -- fall back
    # deterministically instead of leaving it unassigned.
    fallback = pools[0]
    for n in nodes:
        coloring.setdefault(n, fallback)
    return coloring, pools


def _add_message_event_definitions(xml_text: str, message_event_ids: list) -> str:
    """Mark the given start/end event ids as message events.

    pm4py's BPMN object model has no concept of an event definition, so its
    exporter never writes a <bpmn:messageEventDefinition> for a plain
    StartEvent/EndEvent regardless of subclass -- without one a viewer
    cannot tell a pool hand-off event apart from an ordinary start/end and
    won't draw the open-envelope icon or accept a message flow attached to
    it. Layout has already run and baked in coordinates by the time this is
    called, so this only ever adds a childless marker element -- no
    position on the diagram changes.
    """
    if not message_event_ids:
        return xml_text
    root = etree.fromstring(xml_text.encode("utf-8"))
    bpmn_ns = root.nsmap.get("bpmn", root.nsmap.get(None, ""))
    tag = f"{{{bpmn_ns}}}messageEventDefinition" if bpmn_ns else "messageEventDefinition"

    ids = set(message_event_ids)
    for el in root.iter():
        if el.get("id") in ids:
            etree.SubElement(el, tag)
    return etree.tostring(
        root, pretty_print=True, xml_declaration=True, encoding="UTF-8"
    ).decode("utf-8")


def _apply_pools_to_bpmn(activity_to_pool_lane: dict, bpmn) -> str:
    """Lay out a flat pm4py BPMN as a pool-based collaboration diagram.

    Builds a fresh BPMN graph with a Collaboration + one Participant per
    pool, clones every node from the flat graph with its `.process` set to
    its pool (via _color_pools), and replaces every cross-pool sequence
    flow with a message hand-off (src -> [message end event]
    --message flow--> [message start event] -> tgt: a sequence flow may not
    cross a pool boundary, and a message flow may not attach to a gateway,
    which is what a plain relabel would routinely do given how gateway-heavy
    Split Miner's output is).

    Layout is then delegated *entirely* to pm4py's own graphviz-based
    layouter (pm4py.objects.bpmn.layout.layouter), which understands
    Participant/Collaboration nodes natively: it renders each pool as a
    graphviz cluster and lays out every node and edge -- across pool
    boundaries -- in a single pass. This replaced an earlier approach built
    around the `powl` package's layouter, which computes one pool/lane-blind
    flat layout first and then repositions nodes into pool/lane bands
    after the fact; that produced a long chain of real, individually
    fixable bugs (lane sizing, pool overlap, pool width, gateway alignment,
    message-event placement) but the *result* stayed unsatisfying even once
    every one of them was fixed, because the fundamental approach fights
    the layout engine instead of using it: graphviz's own clustering
    computes pool-aware positions and edge routes together, so pools come
    out sized to their own content and nothing needs post-hoc row-wrapping,
    de-overlapping or anchor-snapping to stay readable.
    """
    orig_nodes = list(bpmn.get_nodes())
    orig_flows = list(bpmn.get_flows())

    coloring, pools = _color_pools(orig_nodes, orig_flows, activity_to_pool_lane)

    out = BPMN()
    collab_id = "collab_" + uuid.uuid4().hex
    out.add_node(BPMN.Collaboration(id=collab_id, process=collab_id))

    pool_process = {}
    for pool in pools:
        pid = "proc_" + uuid.uuid4().hex
        pool_process[pool] = pid
        out.add_node(
            BPMN.Participant(
                id="part_" + uuid.uuid4().hex, name=pool, process=None, process_ref=pid
            )
        )

    new_of = {}
    for n in orig_nodes:
        pid = pool_process[coloring[n]]
        try:
            clone = type(n)(name=n.get_name(), process=pid)
        except TypeError:
            # A node type whose constructor needs more than name/process
            # (none currently appear in Split Miner's output) -- fall back
            # to a plain task rather than raising, so one odd node type
            # doesn't take down the whole layout.
            clone = BPMN.Task(name=n.get_name(), process=pid)
        out.add_node(clone)
        new_of[n] = clone

    message_event_ids = []
    for f in orig_flows:
        src, tgt = new_of[f.get_source()], new_of[f.get_target()]
        src_pool, tgt_pool = coloring[f.get_source()], coloring[f.get_target()]
        if src_pool == tgt_pool:
            out.add_flow(BPMN.SequenceFlow(src, tgt, process=pool_process[src_pool]))
            continue

        throw = BPMN.MessageEndEvent(name="", process=pool_process[src_pool])
        catch = BPMN.MessageStartEvent(
            name="", process=pool_process[tgt_pool], isInterrupting=True
        )
        out.add_node(throw)
        out.add_node(catch)
        out.add_flow(BPMN.SequenceFlow(src, throw, process=pool_process[src_pool]))
        out.add_flow(BPMN.SequenceFlow(catch, tgt, process=pool_process[tgt_pool]))
        out.add_flow(BPMN.MessageFlow(throw, catch, process=collab_id))
        message_event_ids.append(throw.get_id())
        message_event_ids.append(catch.get_id())

    bpmn_layouter.apply(out)
    raw = get_xml_string(out)
    xml_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    return _add_message_event_definitions(xml_text, message_event_ids)


# Resolved at import, while the cwd is still the project root (the session
# later chdir's into the MP-SPDZ run's WorkDir).
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Flow-count budget above which every graphviz layout step is skipped.
# Measured locally with the production epsilon=0.5 / eta=0.25, timing one
# save_vis_bpmn call (_apply_pools_to_bpmn then adds one more dot run over
# the whole flat graph -- Node.js isn't on PATH here, so its own initial
# layout step always falls back to graphviz -- so the real cost is roughly
# double these):
#
#   BPI_Challenge_2013_incidents      4 acts ->     20 flows    0.0 s
#   sepsis_by_department             16 acts ->     95 flows    0.0 s
#   Sepsis_Cases                     16 acts ->    241 flows    0.1 s
#   BPIC17_Offer_log                  8 acts ->     75 flows    0.0 s
#   BPI_Challenge_2018               41 acts ->    686 flows   15.2 s
#   Hospital_log                    624 acts -> 69189 flows   did not finish
#
# The growth is steeply superlinear, so this sits just above the largest log
# that actually lays out rather than near the one that does not.
_MAX_LAYOUT_FLOWS = 5000


def split_miner_on_dfg_with_frequencies() -> None:
    with SocketClientSession() as session:
        session.send_handshake_flag()

        spdfg_values: List[int] = session.receive_cint_values()
        print(f"Received spdfg vector of {len(spdfg_values)} from MPC: {spdfg_values}")

        self_loop_values: List[int] = session.receive_cint_values()
        print(f"Received self loop vector of {len(self_loop_values)} from MPC: {self_loop_values}")

        concurrency_values: List[int] = session.receive_cint_values()
        print(f"Received concurrency vector of {len(concurrency_values)} from MPC: {concurrency_values}")

        activities: List[str] = json.loads(f'{session.receive_string()}')
        print(f"Received activities: {activities}")

        activity_binning: List[int] = session.receive_cint_values()
        print(f"Received activity binning: {activity_binning}")

        num_of_organizations = int(len(activity_binning) / len(activities))

        activity_to_pool_lane = dict()
        for i, act in enumerate(activities):
            present_orgs = list()
            for org_id in range(num_of_organizations):
                if activity_binning[org_id * len(activities) + i]:
                    present_orgs.append(str(org_id))
            activity_to_pool_lane[act] = (f"Org_{"_".join(present_orgs)}", None)

        activities_with_start_end_collapsed = activities.copy()
        activities_with_start_end_collapsed.append("__start__end__combined__")

        start_label = "__start__"
        end_label = "__end__"

        activities.append(end_label)
        activities.append(start_label)

        num_of_activities = len(activities)

        edges: set[tuple[str, str]] = set()

        for i, act_from in enumerate(activities):
            for j, act_to in enumerate(activities):
                if spdfg_values[i * num_of_activities + j]:
                    edges.add((act_from, act_to))

        dfg: FilterResult = FilterResult(
            edges=edges,
            source=start_label,
            sink=end_label,
        )
        print(dfg)

        concurrent_pairs = set()

        for i, act_from in enumerate(activities_with_start_end_collapsed):
            for j, act_to in enumerate(activities_with_start_end_collapsed):
                if concurrency_values[i * (num_of_activities - 1) + j]:
                    # WorkingGraph.is_concurrent looks the relation up as
                    # `frozenset((a, b)) in self.concurrency`, so a tuple would
                    # never match and every split would collapse to an XOR.
                    concurrent_pairs.add(frozenset((act_from, act_to)))

        concurrencies: ConcurrencyResult = ConcurrencyResult(
            pdfg=dict(),
            concurrent_pairs=concurrent_pairs,
        )

        print(concurrencies)

        # Self-loops arrive as their own vector, because the pruning strips them
        # from the s-FPDFG and nothing downstream can recover them. The trailing
        # entry is the combined start/end pseudo-activity, whose diagonal counts
        # empty traces and is not a self-loop.
        #
        # Short loops are not transmitted at all: the MPC side consumes them for
        # the concurrency oracle and the pruning, and pm4py discards
        # LoopInfo.short_loops in do_build_initial_bpmn -- WorkingGraph has no
        # field for them. Their effect is already present in the s-FPDFG, which
        # retains both directions of a short-looping pair.
        self_loops: set[str] = set()

        for i, act in enumerate(activities_with_start_end_collapsed[:-1]):
            if self_loop_values[i]:
                self_loops.add(act)

        loops: LoopInfo = LoopInfo(
            self_loops=self_loops,
            short_loops=set(),
            short_loop_freq=dict(),
        )

        print(loops)

        split_miner_instance = split_miner()
        wg = split_miner_instance.do_build_initial_bpmn(dfg, concurrencies, loops, None)

        split_miner_instance.do_discover_splits(wg, None)
        split_miner_instance.do_discover_joins(wg, None)

        split_miner_instance.do_minimize_or_joins(wg, None)

        bpmn = split_miner_instance.do_export_bpmn(wg, None)

        # parameters = {
        #     SmParameters.EPSILON: 0.2,
        #     SmParameters.ETA: 0.0,
        #     SmParameters.OR_MINIMISE: False,
        # }
        # bpmn: pm4py.BPMN = classic.apply(dfg, parameters)

        # Anchor the output to this script's directory: the session has chdir'd
        # into the MP-SPDZ run's WorkDir, so a relative path would land there.
        out_dir = os.path.join(_PROJECT_DIR, "model_output", "bpmn")
        os.makedirs(out_dir, exist_ok=True)

        n_nodes, n_flows = len(bpmn.get_nodes()), len(bpmn.get_flows())
        print(f"Discovered BPMN: {n_nodes} nodes, {n_flows} flows", flush=True)

        if n_flows > _MAX_LAYOUT_FLOWS:
            # Unstructured logs make Split Miner emit a hairball: the hospital
            # log's 624 activities come out as ~7200 nodes / ~69000 flows,
            # almost all of them gateway arcs (~6600 gateways vs 624 tasks,
            # single gateways reaching in-degree 1400). dot cannot rank and
            # mincross that in any output format -- measured locally, it does
            # not finish in 15 min even with splines=line, nslimit=1 and
            # mclimit=0.01, and this image ships only the dot engine. Both
            # save_vis_bpmn and _apply_pools_to_bpmn's flat-layout step go
            # through dot (via its Node.js-unavailable fallback), so both
            # have to be skipped; otherwise the MPC parties sit in "Waiting for client to finish
            # computation" forever. The diagram would be unreadable at this
            # size anyway, so emit the model without diagram interchange
            # coordinates -- it is still valid BPMN and importers re-layout.
            print(
                f"Skipping graphviz layout: {n_flows} flows exceeds the "
                f"{_MAX_LAYOUT_FLOWS} budget (dot does not terminate at this "
                f"size). Writing BPMN without DI coordinates.",
                flush=True,
            )
            raw = bpmn_etree_exporter.get_xml_string(bpmn, parameters={})
            xml_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            xml_path = os.path.join(out_dir, "file-without-lanes.bpmn")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(xml_text)
            print(f"Saved unlaid-out BPMN XML to {xml_path}", flush=True)
        else:
            path = os.path.join(out_dir, "file.svg")
            pm4py.save_vis_bpmn(bpmn, path)

            print(f"Saved BPMN to {path}", flush=True)

            raw = bpmn_etree_exporter.get_xml_string(bpmn, parameters={})
            xml_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            xml_path = os.path.join(out_dir, "file-without-lanes.bpmn")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(xml_text)
            print(f"Saved unlaid-out BPMN XML to {xml_path}", flush=True)

            bpmn_with_lanes = _apply_pools_to_bpmn(activity_to_pool_lane, bpmn)

            xml_path = os.path.join(out_dir, "file.bpmn")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(bpmn_with_lanes)
            print(f"Saved pooled BPMN XML to {xml_path}", flush=True)

        # processed = transform_values(values)
        # print(f"Processed: {processed}")

        # session.send_cint_values(processed)
        # print("Sent processed values back to MPC")

        session.send_handshake_flag(flag=2)


if __name__ == "__main__":
    split_miner_on_dfg_with_frequencies()
