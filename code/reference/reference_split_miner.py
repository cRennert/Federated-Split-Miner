"""Cleartext reference implementation of the federated Split Miner.

Every artifact is computed exactly as the paper's Section 4 defines it and
Section 5's protocols compute it, over N = m + 2 nodes (the m activities plus the
two case boundaries).  The second half of the algorithm -- split and join
discovery, OR-join minimisation, BPMN export -- is the unmodified pm4py
SplitMinerFramework, which is what ``split_miner_socket.py`` drives once the MPC
parties have revealed the artifacts.

The point of this module is to be able to check an MPC run, or a figure in the
paper, against the definitions without standing up MP-SPDZ.  It takes either a
single log or a set of partial logs; with partial logs it merges them the way
Protocol 6 does, so that a federated run and a centralized run can be compared
on the same footing.

    from reference_split_miner import discover
    art = discover(["org0.xes.gz", "org1.xes.gz"], epsilon=0.1, eta=0.4)
    art.write_bpmn("model.bpmn")
"""
from __future__ import annotations

import gzip
import math
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction

START_LABEL = "__start__"
END_LABEL = "__end__"


# --------------------------------------------------------------------------- #
# Step 1: reading and merging the partial logs (protocols 5 and 6)
# --------------------------------------------------------------------------- #
def read_log(path: str) -> list[tuple[str, str, str]]:
    """(case, activity, timestamp) per event, in file order.  Streams, so the
    largest logs do not have to fit in memory as a DOM."""
    opener = gzip.open if path.endswith(".gz") else open
    events: list[tuple[str, str, str]] = []
    with opener(path, "rb") as fh:
        case = act = ts = None
        in_event = False
        for ev, el in ET.iterparse(fh, events=("start", "end")):
            tag = el.tag.rsplit("}", 1)[-1]
            if ev == "start":
                if tag == "trace":
                    case = None
                elif tag == "event":
                    in_event, act, ts = True, None, None
            elif tag == "string" and el.get("key") == "concept:name":
                if in_event:
                    act = el.get("value")
                elif case is None:
                    case = el.get("value")
            elif tag == "date" and el.get("key") == "time:timestamp":
                if in_event:
                    ts = el.get("value")
            elif tag == "event":
                if act is not None:
                    events.append((case, act, ts))
                in_event = False
                el.clear()
            elif tag == "trace":
                el.clear()
    return events


def _norm_ts(t: str | None) -> str:
    """A sortable form of the timestamp.

    An aware timestamp is folded to UTC explicitly rather than through
    ``astimezone()`` without argument: the latter asks the operating system for
    the local zone and raises on Windows for the pre-epoch and far-future dates
    that some logs carry (BPI Challenge 2019 has both).
    """
    if not t:
        return ""
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return t
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def merge(partials: list[list[tuple[str, str, str]]]):
    """Protocol 6.  The parties' event matrices are concatenated in party-index
    order and stably sorted on (case ID, timestamp), so a tie in the timestamp
    resolves to the lower party index -- exactly what the oblivious radix sort
    does, since it is stable."""
    rows = []
    for org, evs in enumerate(partials):
        for pos, (case, act, ts) in enumerate(evs):
            rows.append((case, _norm_ts(ts), org, pos, act))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return rows


# --------------------------------------------------------------------------- #
# Steps 2-8: the artifacts
# --------------------------------------------------------------------------- #
@dataclass
class Artifacts:
    activities: list[str]
    dfg: dict                      # (a,b) -> frequency, boundaries included
    self_loops: set
    short_loops: set               # (a,b) with <a,b,a> in some trace
    concurrency: set               # frozenset pairs, before sanitization
    pruned: dict
    forward: set
    backward: set
    eta_arcs: set
    cutoff: int
    filtered: set
    sanitized: set
    sanitized_concurrency: set      # R*, what the model is built from
    disclosed_concurrency: set      # R* masked to the pairs a split can read
    orphans: set                    # lost predecessors, successors, or both
    binning: dict = field(default_factory=dict)
    n_events: int = 0
    n_cases: int = 0

    # ---- the unchanged pm4py second half ---------------------------------- #
    def build_bpmn(self):
        from pm4py.algo.discovery.split_miner.variants.abc import SplitMinerFramework
        from pm4py.algo.discovery.split_miner.dtypes.loops import LoopInfo
        from pm4py.algo.discovery.split_miner.bpmn_init.classic import ConcurrencyResult
        from pm4py.algo.discovery.split_miner.filtering.abc import FilterResult

        edges = {(START_LABEL if u == ">" else u, END_LABEL if v == "#" else v)
                 for (u, v) in self.sanitized}
        sm = SplitMinerFramework()
        wg = sm.do_build_initial_bpmn(
            FilterResult(edges=edges, source=START_LABEL, sink=END_LABEL),
            # The masked relation, because that is the one the protocol reveals
            # and therefore the only one the parties ever hold. Masking cannot
            # change the model: split discovery reads no pair outside it.
            ConcurrencyResult(pdfg=dict(),
                              concurrent_pairs=set(self.disclosed_concurrency)),
            LoopInfo(self_loops=set(self.self_loops), short_loops=set(),
                     short_loop_freq=dict()),
            None)
        sm.do_discover_splits(wg, None)
        sm.do_discover_joins(wg, None)
        sm.do_minimize_or_joins(wg, None)
        return sm.do_export_bpmn(wg, None)

    def write_bpmn(self, path: str):
        from pm4py.objects.bpmn.exporter.variants import etree as exporter
        bpmn = self.build_bpmn()
        raw = exporter.get_xml_string(bpmn, parameters={})
        xml = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
        return bpmn


def _transitive_closure(reach: list[int], m: int) -> list[int]:
    """Closure by repeated squaring, rows held as bitmasks -- the same doubling
    the protocol does, but one machine word at a time instead of one ABB
    multiplication per entry."""
    for _ in range(max(1, math.ceil(math.log2(m or 2)))):
        new = []
        for a in range(m):
            acc = reach[a]
            row = reach[a]
            while row:
                k = (row & -row).bit_length() - 1
                acc |= reach[k]
                row &= row - 1
            new.append(acc)
        if new == reach:
            break
        reach = new
    return reach


def build_artifacts(rows, epsilon=Fraction(1, 10), eta=Fraction(2, 5)) -> Artifacts:
    epsilon, eta = Fraction(epsilon), Fraction(eta)
    acts = sorted({r[4] for r in rows})
    m = len(acts)
    N = m + 2
    S, E = m, m + 1
    ix = {a: i for i, a in enumerate(acts)}
    name = lambda i: ">" if i == S else ("#" if i == E else acts[i])

    # --- Protocol 7 and 9: directly-follows counts and short-loop patterns -- #
    dfg = defaultdict(int)
    short = set()
    case, seq = None, []

    def flush(seq):
        if not seq:
            return
        path = [S] + seq + [E]
        for u, v in zip(path, path[1:]):
            dfg[(u, v)] += 1
        for i in range(len(seq) - 2):
            if seq[i] == seq[i + 2] and seq[i] != seq[i + 1]:
                short.add((seq[i], seq[i + 1]))

    n_cases = 0
    for c, _t, _o, _p, a in rows:
        if c != case:
            flush(seq)
            n_cases += 1
            case, seq = c, []
        seq.append(ix[a])
    flush(seq)

    # --- Protocol 8: self-loops -------------------------------------------- #
    self_loops = {acts[i] for i in range(m) if dfg.get((i, i), 0) > 0}

    # --- Protocol 10: eps-concurrency -------------------------------------- #
    p, q = epsilon.numerator, epsilon.denominator
    conc = set()
    for (a, b), w in list(dfg.items()):
        if a == b or a >= N or b >= N:
            continue
        back = dfg.get((b, a), 0)
        if back == 0:                                   # condition (1)
            continue
        if (a, b) in short or (b, a) in short:          # condition (2)
            continue
        if abs(w - back) * q < (w + back) * p:          # condition (3)
            conc.add(frozenset((a, b)))

    # --- Protocol 11: pruning ---------------------------------------------- #
    pruned = {}
    for (a, b), w in dfg.items():
        if a == b or frozenset((a, b)) in conc:
            continue
        if w >= dfg.get((b, a), 0) or (a, b) in short or (b, a) in short:
            pruned[(a, b)] = w

    # --- Protocols 13-16: filtering ---------------------------------------- #
    def widest(weight, src):
        cap = defaultdict(int)
        cap[src] = float("inf")
        pred, done = {}, set()
        adj = defaultdict(list)
        for (u, v), w in weight.items():
            adj[u].append((v, w))
        for _ in range(N):
            u, best = None, 0
            for i, c in cap.items():
                if i not in done and c > best:
                    u, best = i, c
            if u is None:
                break
            done.add(u)
            for v, w in adj[u]:
                if v in done:
                    continue
                c = min(cap[u], w)
                if c > cap[v]:
                    cap[v], pred[v] = c, u
        return pred

    pred = widest(pruned, S)
    succ = widest({(v, u): w for (u, v), w in pruned.items()}, E)
    forward = {(name(pred[v]), name(v)) for v in pred}
    backward = {(name(v), name(succ[v])) for v in succ}

    # The percentile is taken over the best-frequency arcs, not over every arc:
    # for each node its single most frequent outgoing arc and its single most
    # frequent incoming arc. That is
    # DirectlyFollowGraphPlus.bestEdgesOnMaxFrequencies() followed by
    # computeFilterThreshold(), whose disabled `frequencyOrderedEdges.addAll(edges)`
    # branch is the all-arcs variant. Indexing all arcs from the top instead puts
    # the threshold an order of magnitude lower on a real log.
    #
    # Java sorts that list ascending and indexes it at round(size * eta), so that
    # a larger eta filters harder: eta = 0 admits every arc and eta = 1 only the
    # most frequent ones. We keep that reading, so that a threshold quoted for
    # the Split Miner means here what it means there and the default is 0.4.
    #
    # The frequencies are already sorted ascending, which is also the order the
    # protocol sorts in, so the index is read off them directly rather than
    # counted from the top: pos = min(round(d * eta) + 1, d), the clamp being
    # Java's `if (i == size) i--`.
    #
    # The list is then reduced to its *distinct values*. Java reduces to distinct
    # *edges* instead, because bestEdges is a HashSet and an arc that is both its
    # source's best outgoing and its target's best incoming arc is added twice;
    # that leaves two arcs of equal frequency as two entries, where we keep one.
    # Deduplicating on the value makes the array independent of which arc a tie
    # is resolved to, so no tie-break has to be fixed here or in the protocol.
    out_of, in_to = defaultdict(list), defaultdict(list)
    for (a, b), w in pruned.items():
        out_of[a].append(w)
        in_to[b].append(w)
    values = [max(out_of[n]) for n in range(N) if n != E and out_of[n]]
    values += [max(in_to[n]) for n in range(N) if n != S and in_to[n]]

    weights = sorted({w for w in values if w > 0})       # ascending, distinct
    d = len(weights)
    if d:
        pos = min(math.floor(d * eta + Fraction(1, 2)) + 1, d)
        cutoff = weights[pos - 1]
    else:
        cutoff = 0
    eta_arcs = {(name(a), name(b)) for (a, b), w in pruned.items() if w >= cutoff}
    filtered = forward | backward | eta_arcs

    # --- Protocol 17: sanitization ----------------------------------------- #
    # Each side of an activity is repaired on its own, so an activity that kept
    # its successors but lost its predecessors is attached to the start alone.
    # Orphanhood counts the boundary arcs: an activity that merely ends the
    # process still has its arc to the end and is not cut off from anything.
    # Orphanhood is decided by reachability from the boundary, not by degree.
    # A degree of zero on one side does strand an activity, but so does a whole
    # component severed from the start: eps-concurrency deletes both directions
    # of an arc at once, and where that arc was a component's only entry, every
    # node inside keeps a non-zero degree through the component's own cycles
    # while none of them is reachable any more. Counting arcs leaves those
    # activities in the graph and in the model, where they can never occur.
    def _reachable(arcs, source, forward=True):
        adj = defaultdict(set)
        for (u, v) in arcs:
            (adj[u] if forward else adj[v]).add(v if forward else u)
        seen, stack = {source}, [source]
        while stack:
            u = stack.pop()
            for w in adj[u] - seen:
                seen.add(w)
                stack.append(w)
        return seen

    from_start = _reachable(filtered, ">", True)
    to_end = _reachable(filtered, "#", False)
    orphan_in = {a for a in acts if a not in from_start}
    orphan_out = {a for a in acts if a not in to_end}
    orphans = orphan_in | orphan_out                     # cut off on either side
    sanitized = (set(filtered)
                 | {(">", a) for a in orphan_in}
                 | {(a, "#") for a in orphan_out})

    # An activity is only parallelized if the log recorded it interleaving with
    # something: an activity the pruning stranded is concurrent to whatever took
    # its arcs away, while one that was always a case of its own has the same
    # shape and no such evidence, and parallelizing it would demand it in every
    # case rather than offer it as one.
    ing2, outg2 = defaultdict(set), defaultdict(set)
    for (u, v) in sanitized:
        outg2[u].add(v)
        ing2[v].add(u)
    rec_conc = {acts[a] for pr in conc for a in tuple(pr) if a < m}

    reach = [0] * m
    for (u, v) in sanitized:
        if u in ix and v in ix:
            reach[ix[u]] |= 1 << ix[v]
    for a in range(m):
        reach[a] |= 1 << a
    reach = _transitive_closure(reach, m)

    sconc = set()
    for pair in conc:
        a, b = tuple(pair)
        if a >= m or b >= m:
            continue
        if not ((reach[a] >> b) & 1 or (reach[b] >> a) & 1):
            sconc.add(frozenset((acts[a], acts[b])))
    # An activity that lost its predecessors, its successors, or both, and that
    # the log recorded interleaving with something, is declared concurrent to
    # every activity the repaired graph cannot order against it. The same
    # closure that restricts the relation decides it, so the completion never
    # claims parallelism the graph shows as a sequence.
    for o in orphans & rec_conc:
        i = ix[o]
        for x in acts:
            j = ix[x]
            if x != o and not ((reach[i] >> j) & 1 or (reach[j] >> i) & 1):
                sconc.add(frozenset((o, x)))

    # Split discovery is the only step that reads the relation, and it reads it
    # only among the immediate successors of one node. Every other pair is
    # invisible in the model, so revealing it would say more about the log than
    # the model does.
    #
    # Here the mask only has to give the same model, so where it is applied does
    # not matter. In the protocol it does: the mask is a function of the graph,
    # which is revealed anyway, but it is applied to the shared relation before
    # anything is disclosed, because a mask applied after the disclosure
    # withholds nothing.
    co_successor = set()
    for vs in outg2.values():
        for x in vs:
            for y in vs:
                if x != y and x in ix and y in ix:
                    co_successor.add(frozenset((x, y)))
    disclosed = sconc & co_successor

    return Artifacts(
        activities=acts,
        dfg={(name(a), name(b)): w for (a, b), w in dfg.items()},
        self_loops=self_loops,
        short_loops={(acts[a], acts[b]) for (a, b) in short},
        concurrency={frozenset((name(a), name(b))) for pr in conc
                     for a, b in [tuple(pr)]},
        pruned={(name(a), name(b)): w for (a, b), w in pruned.items()},
        forward=forward, backward=backward, eta_arcs=eta_arcs, cutoff=cutoff,
        filtered=filtered, sanitized=sanitized, sanitized_concurrency=sconc,
        disclosed_concurrency=disclosed,
        orphans=orphans, n_events=len(rows), n_cases=n_cases)


def discover(paths, epsilon=0.1, eta=0.4, binning=True) -> Artifacts:
    """Run the whole framework on one log or on a list of partial logs."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    partials = [read_log(str(p)) for p in paths]
    rows = merge(partials)
    art = build_artifacts(rows, Fraction(str(epsilon)), Fraction(str(eta)))
    if binning:
        seen = defaultdict(set)
        for _c, _t, org, _p, a in rows:
            seen[a].add(org)
        art.binning = {a: sorted(v) for a, v in seen.items()}
    return art
