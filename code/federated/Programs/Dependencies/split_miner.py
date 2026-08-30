import json
from fractions import Fraction
from typing import List

from Compiler.types import Matrix
from Compiler.library import *
from pm4py.algo.discovery.split_miner.dtypes.loops import LoopInfo

from .utils.timers import Timer, TimerContext
from .utils.misc import sanitize_matrix, sanitize_matrix_inverse, sanitize_list, sanitize_list_inverse, mask_excluding_marked_row_columns
from Compiler.oram import LinearORAM
from .demux import DemuxKellerWithoutBitpacking
from .input_validation import validate_event_log
from Compiler.mpc_math import trunc
from .dfg import build_directly_follows_graph_and_its_artifacts_by_leaking_case_length_distribution, build_directly_follows_graph_and_its_artifacts_fully_obliviously


def comparison_bit_length(bound: int, what: str) -> int:
    """Bit length for a comparison whose operands are bounded by ``bound``.

    Both thresholds are cleared of their division by scaling the two sides of the
    inequality with the denominator, which inflates the operands. That inflation
    is silent: an overflow does not raise, it just yields the wrong comparison
    result and hence a wrong concurrency or filtering decision. Since ``bound``
    is known at compile time, we check it here instead and fail loudly.

    The returned length covers the *signed difference* of two operands, which is
    what the comparison protocols actually decompose, hence the extra bit. The
    budget we check against is the compiler's nominal integer width; ring-based
    protocols may need further headroom for statistical masking, which MP-SPDZ
    verifies itself when it compiles the comparison.
    """
    bits = int(bound).bit_length() + 1
    budget = get_program().bit_length
    assert bits <= budget, (
        f"{what}: the scaled operands need {bits} bits, but the program only provides "
        f"{budget}. Either write the threshold with fewer decimal places or increase "
        f"the ring / field size."
    )
    return bits


def detect_self_loops(
    dfg: Matrix
) -> Array:
    return Array.create_from(sanitize_list(dfg.diag()))


def detect_short_loops(
        is_new_case: Array,
        demuxed_events: Matrix,
        number_of_event_types: int,
        n_events: int,
) -> Matrix:
    """Detect the short-loop *pattern* relation, i.e. `a <-> b` iff <a,b,a> occurs.

    The relation is returned as-is: asymmetric, and without excluding activities
    that also have a self-loop.

    Asymmetric, because the two consumers use it differently. eps-concurrency is
    symmetric, so its condition (3) takes the symmetric closure; the pruning
    decides one arc at a time, and a pattern <a,b,a> justifies keeping the arc
    (a,b), while (b,a) still has to earn its place through its own frequency or
    through <b,a,b>. Symmetrising here would over-approximate the pruning.

    Without a self-loop condition, because that condition is not a property of
    the relation but condition (1) of the eps-concurrency definition, which
    applies to the concurrency oracle alone -- the pruned-DFG definition states
    no condition on self-loops. Masking here would put it on the wrong consumer
    *and* invert its effect: it would only *remove* short-loop evidence, which
    weakens condition (3) and makes activities look more concurrent, the exact
    opposite of what condition (1) asks for.
    """
    n_activities = number_of_event_types + 1

    # The events i, i + 1 and i + 2 belong to the same case iff neither i + 1 nor i + 2 starts a
    # new case. This only needs the secret new-case flags, so no case ids have to be compared.
    same_case_id_short_loop = sint.Array(n_events - 2)
    same_case_id_short_loop[:] = (1 - is_new_case.get_part_vector(base=1, size=n_events - 2)) * \
        (1 - is_new_case.get_part_vector(base=2, size=n_events - 2))

    # Filtering: broadcast each flag across all activity columns.
    filter_matrix = sint.Matrix(rows=n_events - 2, columns=n_activities)
    filter_matrix[:] = same_case_id_short_loop[regint.inc(
        (n_events - 2) * n_activities, 0, 1, n_activities)]

    # The compiler's instruction merger corrupts the matrix products below when they share a basic
    # block with the vectorized construction of the filter matrix (observed: an all-zero short loop
    # matrix). The former @for_range-based filter matrix construction provided this barrier
    # implicitly. See the comment in DemuxKellerWithoutBitpacking.demux_batch for the same issue.
    break_point()

    # loop_dfg[a][b] counts the positions at which <a,b,a> occurs within one case.
    loop_dfg = demuxed_events.get_part(start=0, size=n_events - 2)\
        .schur(demuxed_events.get_part(start=2, size=n_events - 2))\
        .schur(filter_matrix)\
        .trans_mul(demuxed_events.get_part(start=1, size=n_events - 2))

    return sanitize_matrix(loop_dfg)


def detect_left_right_positive(
    dfg: Matrix
) -> Matrix:
    sanitized_dfg = sanitize_matrix(dfg)
    return sanitized_dfg.schur(sanitized_dfg.transpose())


def detect_concurrency(
        dfg: Matrix,
        short_loop_matrix: Matrix,
        epsilon: Fraction,
        left_and_right_positive_dfg: Matrix,
        n_events: int
) -> Matrix:
    """Decide eps-concurrency for every pair of activities.

    Implements all three conditions of the eps-concurrency definition:
    (1) both directions of the arc are positive, (2) neither direction forms a
    short-loop pattern, and (3) the weighted imbalance of the two arc weights
    stays below epsilon.

    Conditions (1)-(2) are folded into a single mask, together with the a != b
    exclusion, and applied to the DFG *before* condition (3) is evaluated: a
    masked pair has both arc weights zero, for which the strict inequality of (3)
    is false, so the mask propagates through the comparison without a further
    multiplication. The diagonal must be excluded explicitly: a self-looping
    activity has a positive diagonal entry and a zero imbalance against itself,
    so without ``non_diag_ones`` it would come out concurrent with itself.
    """
    dfg_shape = dfg.shape
    non_diag_ones = dfg.same_shape()
    non_diag_ones.assign_all(1)

    for i in range(dfg_shape[0]):
        non_diag_ones[i][i] = 0

    # Condition (1), plus a != b.
    left_and_right_positive_dfg_no_diag = left_and_right_positive_dfg.schur(non_diag_ones)
    # Condition (2): the symmetric closure of the short-loop pattern relation.
    neither_left_nor_right_short_loop = sanitize_matrix_inverse(short_loop_matrix + short_loop_matrix.transpose())

    dfg_mask = left_and_right_positive_dfg_no_diag\
        .schur(neither_left_nor_right_short_loop)
    masked_dfg = dfg.schur(dfg_mask)

    dfg_vector = masked_dfg.get_vector()
    dfg_transpose_vector = masked_dfg.transpose().get_vector()

    left_minus_right = dfg_vector - dfg_transpose_vector
    left_minus_right_abs = left_minus_right.less_than(0).if_else(-left_minus_right, left_minus_right)
    left_plus_right = dfg_vector + dfg_transpose_vector

    # Condition (4): |F(a,b) - F(b,a)| / (F(a,b) + F(b,a)) < epsilon. Since the
    # denominator is non-negative, the division clears to
    # |F(a,b) - F(b,a)| * den < (F(a,b) + F(b,a)) * num for epsilon = num/den.
    # Keeping this in the integers matters: a fixed-point epsilon is off by a few
    # units in the last place, which flips the strict comparison for pairs that
    # sit exactly on the threshold.
    numerator, denominator = epsilon.numerator, epsilon.denominator

    # Each arc weight is at most the number of events, so the left-hand side is
    # bounded by n_events * den and the right-hand side by 2 * n_events * num.
    bit_length = comparison_bit_length(
        2 * n_events * max(numerator, denominator), "eps-concurrency")

    concurrency_vector = (left_minus_right_abs * denominator).less_than(
        left_plus_right * numerator, bit_length=bit_length)
    concurrency_matrix = dfg.same_shape()
    concurrency_matrix.assign_vector(concurrency_vector)

    return concurrency_matrix


def dfg_pruning(
    dfg: Matrix,
    concurrency_matrix: Matrix,
    short_loop_matrix: Matrix,
    self_loops: Array
) -> Matrix:
    """Prune the DFG, following the pruned-DFG definition.

    An arc (a,b) survives iff it connects two non-concurrent activities and is
    either the stronger of the two directions or part of a short-loop pattern in
    either direction, i.e. ``not (a || b) and (F(a,b) >= F(b,a) or a <-> b or
    b <-> a)``. The short-loop disjunct keeps both directions of a do-redo
    pattern, which would otherwise lose its weaker direction and could
    disconnect the redo activity; it is symmetric because the evidence a
    subtrace <a,b,a> carries concerns the pair and not one of its two arcs.

    Removing the self-loops -- a separate step in the framework -- is folded into
    the same mask by clearing the diagonal.
    """
    inverse_concurrency_matrix = dfg.same_shape()
    inverse_concurrency_matrix.assign_all(1)
    inverse_concurrency_matrix = inverse_concurrency_matrix - concurrency_matrix

    dfg_vector = dfg.get_vector()
    dfg_vector_trans = dfg.transpose().get_vector()

    smaller_than_matrix = dfg.same_shape()
    smaller_than_matrix.assign_vector(dfg_vector.less_than(dfg_vector_trans))

    inverse_smaller_than_matrix = dfg.same_shape()
    inverse_smaller_than_matrix.assign_all(1)
    inverse_smaller_than_matrix = inverse_smaller_than_matrix - smaller_than_matrix

    # a <-> b or b <-> a. All operands below are 0/1, so x or y = x + y - x*y.
    short_loop_transposed = short_loop_matrix.transpose()
    any_short_loop = short_loop_matrix + short_loop_transposed \
        - short_loop_matrix.schur(short_loop_transposed)

    # F(a,b) >= F(b,a) or the pair alternates in either direction.
    stronger_or_short_loop = inverse_smaller_than_matrix + any_short_loop \
        - inverse_smaller_than_matrix.schur(any_short_loop)

    mask = inverse_concurrency_matrix.schur(stronger_or_short_loop)

    dim = len(self_loops)
    for i in range(dim):
        mask[dim - 1][i] = 1
        mask[i][dim - 1] = 1
        mask[i][i] -= self_loops[i]

    result = dfg.schur(mask)

    return result


def find_sources_and_sinks(
    pdfg: Matrix
) -> tuple[Array, Array]:
    dim = pdfg.shape[0]
    mask = sint.Matrix(rows=1, columns=dim)
    mask.assign_vector(sint(1, size=dim))
    mask[0][dim-1] = 0

    sources = mask.mul(pdfg).get_vector().less_than(cint(1, dim))
    sinks = mask.mul_trans(pdfg).get_vector().less_than(cint(1, dim))

    return sources, sinks


def reformat_start_and_end(
        pdfg: Matrix,
) -> Matrix:
    dim = pdfg.shape[0]
    result = sint.Matrix(rows=dim + 1, columns=dim + 1)
    result.assign_all(0)

    for i in range(dim - 1):
        result.assign_part_vector(vector=pdfg.get_part_vector(i), base=i)

    result.assign_part_vector(vector=pdfg.get_part_vector(dim-1), base=dim)

    return result


def strip_start_and_end(
    pdfg: Matrix,
) -> Matrix:
    dim = pdfg.shape[0]

    result = sint.Matrix(rows=dim-1, columns=dim-1)
    result.assign_all(0)

    for i in range(dim):
        result.assign_part_vector(pdfg.get_part_vector(i), base=i)

    return result


def find_best_incoming_edges_dijkstra(
    pdfg: Matrix,
    source: int,
    n_events: int,
    infinity: int = 2**40
) -> Array:
    """
    Based on Ehrmanntraut and Meyer's Dijkstra predecessor-calculation protocol [1, Prot 3].

    [1]: TODO
    """

    def permute_adjacency_matrix(m: Matrix, permutation, reverse: bool = False) -> Matrix:
        res = m.same_shape()
        res[:] = m[:]
        res.secure_permute(permutation, reverse)
        res = res.transpose()
        res.secure_permute(permutation, reverse)
        res = res.transpose()
        return res

    def unitvector(i: int | cint, length: int, value_type: type[cint] | type[sint] = cint) -> Array:
        result = Array(length, value_type=value_type)
        result.assign_all(0)
        result[i] = 1
        return result

    def reveal_argmax_of_unvisited(distances: Array, visited: Array, number_of_unvisited_nodes: int) -> cint:
        # Filter out visited nodes.
        unvisited_distances = sint.Array(number_of_unvisited_nodes)
        map_to_node = cint.Array(number_of_unvisited_nodes)
        pos = cint(0)

        @for_range(len(distances))
        def build_unvisited_distances(i: cint) -> None:
            @if_(visited[i] == 0)
            def fill_value() -> None:
                unvisited_distances[pos] = distances[i]
                map_to_node[pos] = i
                pos.iadd(1)

        # Calculate the argmax of the unvisited distances.
        def argmax_op(a, b):
            comp = (a[1] >= b[1])
            return comp.if_else(a[0], b[0]), comp.if_else(a[1], b[1])

        argmax = tree_reduce(argmax_op, enumerate(unvisited_distances))[0]

        # Reveal the result, map back to original array index.
        if isinstance(argmax, sint):
            # Normal case, but still an if, as argmin returns an int when there is only a single unvisited node.
            argmax = argmax.reveal()
        # print_ln("argmin=%s", map_to_node[result])
        return map_to_node[argmax]

    n_nodes = pdfg.shape[0]
    permutation = sint.get_secure_shuffle(n_nodes)

    # Copy the pdfg to not overwrite it.
    edge_capacity_matrix = sint.Matrix(n_nodes, n_nodes)
    edge_capacity_matrix[:] = pdfg[:]

    inverse_permutation = Array.create_from(sint(regint.inc(n_nodes)))
    predecessors = sint.Array(n_nodes)
    predecessors.assign_all(n_nodes)

    edge_capacity_matrix = permute_adjacency_matrix(edge_capacity_matrix, permutation)
    inverse_permutation.secure_permute(permutation)
    predecessors.secure_permute(permutation)
    current_node = (regint.inc(n_nodes) * unitvector(source, n_nodes, value_type=sint)[:].secure_permute(permutation)).sum().reveal()

    node_capacities = sint.Array(n_nodes)
    node_capacities.assign_all(0)
    node_capacities[current_node] = infinity

    visited = cint.Array(n_nodes)
    visited.assign_all(0)

    for i in range(n_nodes):
        # Mark the current node as visited.
        # 0 rounds, 0 comm, O(n) comp.
        visited[current_node] = 1

        outgoing_edge_capacities = edge_capacity_matrix[current_node]
        current_node_capacity_as_array = sint.Array(n_nodes)
        current_node_capacity_as_array.assign_all(node_capacities[current_node])

        current_node_as_arr = sint.Array(n_nodes)
        current_node_as_arr.assign_all(inverse_permutation[current_node])

        # We calculate the possible updated capacity of neighboring nodes as the minimum of the current node's
        # capacity and the edge capacities.
        capacity_via_current_node = (current_node_capacity_as_array[:] < outgoing_edge_capacities[:]) \
            .if_else(current_node_capacity_as_array[:], outgoing_edge_capacities[:])

        # Now we update neighbors whose capacity over the current node is larger than the
        # current capacity.
        is_better = capacity_via_current_node > node_capacities[:]
        node_capacities[:] = is_better.if_else(capacity_via_current_node, node_capacities[:])
        predecessors[:] = is_better.if_else(current_node_as_arr[:], predecessors[:])
        break_point()

        if i < n_nodes - 1:
            # Use the inverse permutation as tiebreaker when multiple nodes have the capacity.
            # As discussed in [1, Appendix A], this prevent a small leakage.
            tiebreak_capacities = Array.create_from(n_nodes * node_capacities[:] + inverse_permutation[:])
            current_node = reveal_argmax_of_unvisited(tiebreak_capacities, visited, n_nodes-i-1)
            break_point()

    predecessors.secure_permute(permutation, reverse=True)
    return predecessors


def find_best_incoming_edges_oram(
    pdfg: Matrix,
    source: int,
    n_events: int,
) -> Array:
    dim = pdfg.shape[0]

    queue = LinearORAM(size=dim)
    currently_in_queue = LinearORAM(size=dim)
    head = sint(0)
    tail = sint(1)
    queue[head] = source
    currently_in_queue[source] = 1

    capacities = sint.Array(size=dim)
    capacities.assign_all(0)
    # Capacities cannot exceed n_events - 1, representing an unreachable (infinite) value
    capacities.assign(other=n_events, base=source)

    best_incoming_edges = sint.Array(size=dim)
    best_incoming_edges.assign_all(dim)

    visited = sint.Array(size=dim)
    visited.assign_all(0)

    @while_do(lambda: head.not_equal(tail).reveal())
    def _():
        curr_node_index = queue[head]
        currently_in_queue[curr_node_index] = 0
        curr_node_row_matrix = DemuxKellerWithoutBitpacking.demux(curr_node_index, n=dim).to_row_matrix()

        outgoing_frequencies = curr_node_row_matrix.mul(pdfg)
        outgoing_edge_mask = outgoing_frequencies.get_vector().greater_than(0)

        curr_capacity = curr_node_row_matrix.mul(capacities.to_column_matrix())[0][0]

        capacity_comparison = outgoing_frequencies.get_vector().greater_than(curr_capacity)
        capacity_comparison_masked = outgoing_edge_mask.if_else(capacity_comparison, 0)

        new_max_capacities = capacity_comparison_masked.if_else(
            curr_capacity,
            outgoing_frequencies.get_vector(),
        )

        capacity_improvement = new_max_capacities.greater_than(capacities.get_vector())

        capacities.assign_vector(capacity_improvement.if_else(new_max_capacities, capacities.get_vector()))
        best_incoming_edges.assign_vector(capacity_improvement.if_else(curr_node_index, best_incoming_edges))

        for i in range(dim):
            queue[tail] = i
            gets_updated = capacity_improvement[i].if_else(currently_in_queue[i].bit_not(), 0)
            tail.iadd(gets_updated)
            currently_in_queue[i] = gets_updated.if_else(1, currently_in_queue[i])
            tail.update(tail.greater_equal(dim).if_else(0, tail))

        head.iadd(1)
        head.update(head.greater_equal(dim).if_else(0, head))

    return best_incoming_edges


def find_best_outgoing_edges(
    pdfg: Matrix,
    sink: int,
    n_events: int,
) -> Array:
    return find_best_incoming_edges_dijkstra(
        pdfg=pdfg.transpose(),
        source=sink,
        n_events=n_events,
    )


def keep_eta_percentile_edges(
    pdfg: Matrix,
    eta: Fraction,
) -> Matrix:
    """Keep the edges whose frequency reaches the eta-percentile threshold.

    The percentile is taken over the best-frequency edges, not over every
    positive edge: for each node its single most frequent outgoing edge and its
    single most frequent incoming edge. That is what
    ``DirectlyFollowGraphPlus.bestEdgesOnMaxFrequencies`` collects and what
    ``computeFilterThreshold`` then indexes; the all-edges variant sits in that
    method as a disabled ``frequencyOrderedEdges.addAll(edges)`` branch. The list
    is sorted *ascending* and indexed at ``round(d * eta)``, so a larger eta
    filters harder. Taking the percentile over all edges and indexing from the
    top instead -- as an earlier version of this function did -- puts the
    threshold an order of magnitude lower on a real log and keeps far too much.

    The list is reduced to its distinct *values*. Java reduces to distinct
    *edges*, because ``bestEdges`` is a ``HashSet`` and an edge that is both its
    source's best outgoing and its target's best incoming edge is added twice;
    two different edges of equal frequency stay two entries there and become one
    here. Deduplicating on the value is also what keeps this protocol cheap: no
    edge ever has to be identified, so the maxima are taken as plain frequencies
    and no secret index is ever selected, demultiplexed or tie-broken.

    There is no shortcut for ``eta = 1``. Under the old all-edges percentile it
    kept every positive edge, but here it selects the *largest* of the distinct
    best-frequency values, so it filters hardest rather than not at all -- on the
    running example it leaves one edge where returning the graph unchanged would
    leave fifteen. The whole range is handled by the rank comparison below.
    """
    n = pdfg.shape[0]
    size = 2 * n + 1
    transposed = pdfg.transpose()

    # --- the frequencies the percentile is taken over ---------------------- #
    # One entry per node per direction: the frequency of its most frequent
    # outgoing edge and of its most frequent incoming edge. Only the frequencies
    # are wanted, never the edges carrying them, so a tournament of maxima is
    # enough and no index is ever selected or demultiplexed.
    #
    # Each tournament compares whole rows at once, so it takes log2(n) rounds
    # rather than one per node. Reducing over the rows of the transpose leaves
    # every row's maximum, and reducing over the rows of the matrix every
    # column's.
    def larger(left, right):
        return left.greater_than(right).if_else(left, right)

    best_out = tree_reduce(larger, [transposed[j].get_vector() for j in range(n)])
    best_in = tree_reduce(larger, [pdfg[i].get_vector() for i in range(n)])

    # The end node has no outgoing edge and the start node no incoming one, so
    # their entries are zero and the two nodes Java skips need no case of their
    # own -- the deduplication below drops zeroes anyway. The one extra slot pads
    # the array so that every entry has a predecessor to be compared against.
    values = Array(size, sint)
    values.assign_all(0)
    values.assign_vector(best_out, base=0)
    values.assign_vector(best_in, base=n)
    values.sort()

    # --- the distinct values ----------------------------------------------- #
    # Once sorted, the step from one entry to the next is zero exactly where a
    # value repeats and positive exactly where a new distinct value begins. One
    # subtraction and one comparison therefore deduplicate the array, and the
    # prefix sum of those marks is the rank of every entry among the distinct
    # values -- which is what a second sort would otherwise have to earn by
    # moving the survivors into a contiguous block.
    steps = (values.get_vector(base=1, size=size - 1)
             - values.get_vector(base=0, size=size - 1))
    ranking = steps.greater_than(0).prefix_sum()
    d = ranking.get_vector(base=size - 2, size=1)

    # --- the threshold ------------------------------------------------------ #
    # The threshold is the distinct value of rank round(d * eta) + 1, and the
    # steps up to that rank telescope to precisely that value. The padding zero
    # is the smallest entry, so the sum starts from zero and needs no offset.
    #
    # `eta` says how much behavior to filter away, as in the papers and in the
    # Java implementation, and the weights are already sorted ascending, so the
    # threshold sits at `pos = min(round(d * eta) + 1, d)` counted from the
    # smallest -- Java's own index, with its clamp.
    #
    # `eta = p/q` is public and exact, so clearing the division leaves a
    # comparison between two secret linear terms:
    #     rank <= pos             with pos = floor(d * p / q + 1/2) + 1
    #     iff  rank - 1 <= d * p / q + 1/2                  | * 2 * q
    #     iff  2 * q * rank <= 2 * p * d + 3 * q.
    # An integer is at most a floor exactly when it is at most its argument, the
    # factor two clears the half and `q` clears the division.
    # A ceiling is at most an integer m exactly when its argument is; the factor
    # two clears the half and `q` clears the division. The clamp of `pos` needs no
    # counterpart, as a rank never exceeds `d` anyway. Every term stays an
    # integer, so the rank cannot land on the wrong side of one -- unlike the
    # double arithmetic on the Java side, where 45 * 0.7 = 31.499999999999996
    # rounds the wrong way.
    #
    # The clamp of `pos` needs no counterpart here: a rank never exceeds `d`, so a
    # cutoff rank running past the last distinct value simply lets the sum cover
    # every step there is and stop at the largest value, which is what Java has to
    # correct after the fact with `if (i == size) i--`.
    numerator, denominator = eta.numerator, eta.denominator
    bit_length = comparison_bit_length(
        2 * size * max(numerator, denominator) + 3 * denominator,
        "eta-percentile rank")
    # Built around an `sint` so that a share of zero stays secret-typed: `d * 0`
    # folds to a plain Python int, which carries none of the vector operations.
    wanted = sint(3 * denominator) + d * (2 * numerator)
    reached = (2 * denominator * ranking).less_equal(
        wanted.expand_to_vector(size - 1), bit_length=bit_length)
    minimal_value = (reached * steps).sum()

    # With no edge at all every step is zero, and a threshold of zero would admit
    # the absent edges. Lifting it to one rejects them, which spares a mask over
    # the whole matrix for a case that costs a single comparison here.
    minimal_value = minimal_value + 1 - d.greater_than(0)

    sanitized_pdfg = pdfg.same_shape()
    sanitized_pdfg.assign_vector(pdfg.get_vector().greater_equal(minimal_value))

    return sanitized_pdfg


def sanitize_conc_matrix(concurrency_matrix: Matrix,
                         spdfg: Matrix,
                         numb_of_activities: int) -> tuple[Matrix, Matrix]:
    """Reconnect orphaned activities, restrict concurrency, and disclose.

    Returns the revealed graph and the relation masked to the pairs a split can
    read, i.e. R^split rather than R^san: the graph is opened inside this
    protocol precisely so that the mask can be applied before the relation
    leaves it.

    ``spdfg`` is indexed by ``[activities..., end, start]`` (see
    :func:`reformat_start_and_end`), while ``concurrency_matrix`` is indexed by
    ``[activities..., start/end combined]``. Only the leading
    ``numb_of_activities`` indices are shared between the two.

    An activity is *orphaned* when it has no predecessor among the other
    activities and no edge from the start (analogously for successors and the
    end). Such an activity is reconnected to the start / the end so that it
    forms its own ``start -> a -> end`` branch.

    Losing *either* side is enough for the relation to have to take over, since
    the missing side is exactly where the evidence for placing the activity is
    gone. The completion is applied only if the log recorded the activity
    interleaving with something -- an activity that was always a case of its own
    is orphaned in the same way without ever having been concurrent, and
    parallelising it would demand it in every case rather than offer it as one --
    and only against the activities the graph cannot order against it, which is
    the same condition that restricts the relation.

    Treating one-sided and two-sided orphans alike is what keeps the disclosure
    argument intact: a completion that fired only on both sides would let an
    observer of the relation tell the two apart, a distinction the model does not
    draw.
    """
    start = spdfg.shape[0] - 1
    end = spdfg.shape[0] - 2

    # Activity-to-activity block of the s-pDFG, i.e. the s-pDFG without its
    # start and end node. A self-loop does not connect an activity to another
    # one, so it must not count towards the (in|out)-degrees below.
    activity_spdfg = sint.Matrix(rows=numb_of_activities, columns=numb_of_activities)
    for i in range(numb_of_activities):
        activity_spdfg[i].assign_vector(spdfg[i].get_vector(base=0, size=numb_of_activities))
        activity_spdfg[i][i] = 0

    ones_column = sint.Matrix(rows=numb_of_activities, columns=1)
    ones_column.assign_all(1)

    from_start = spdfg[start].get_vector(base=0, size=numb_of_activities)
    to_end = sint.Array(numb_of_activities)
    for i in range(numb_of_activities):
        to_end[i] = spdfg[i][end]

    # Two activities can only be concurrent if neither reaches the other, so
    # accumulate all path lengths up to numb_of_activities - 1. The same closure
    # decides orphanhood below, so it is computed before the reconnection --
    # which is sound, as the reconnection only ever adds start and end edges and
    # those are not part of the activity-to-activity block.
    reachability = activity_spdfg.same_shape()
    reachability[:] = activity_spdfg[:]
    for i in range(numb_of_activities):
        reachability[i][i] = 1

    @for_range(math.ceil(math.log2(numb_of_activities - 1)))
    def square_step(_: cint) -> None:
        reachability[:] = (reachability.direct_mul(reachability)) > 0

    # An activity is orphaned when the boundary can no longer reach it, or it
    # can no longer reach the boundary. Counting its arcs instead would miss a
    # whole component severed from the start: eps-concurrency deletes both
    # directions of an arc at once, and where that arc was a component's only
    # entry, every node inside keeps a non-zero degree through the component's
    # own cycles while none of them is reachable any more. Such activities would
    # stay in the graph, and in the model, where they can never occur.
    from_start_column = sint.Matrix(rows=numb_of_activities, columns=1)
    from_start_column.assign_vector(from_start)
    to_end_column = sint.Matrix(rows=numb_of_activities, columns=1)
    to_end_column.assign_vector(to_end.get_vector())

    # (R^T f)[a] = sum_k f[k] R[k][a]: some activity the start reaches leads to a.
    reached_from_start = reachability.transpose().mul(from_start_column).get_vector()
    # (R t)[a] = sum_k R[a][k] t[k]: a leads to some activity that reaches the end.
    reaches_end = reachability.mul(to_end_column).get_vector()

    orphaned_activity_in = Array.create_from(
        sanitize_list_inverse(Array.create_from(reached_from_start)))
    orphaned_activity_out = Array.create_from(
        sanitize_list_inverse(Array.create_from(reaches_end)))
    orphaned_activity = Array.create_from(
        orphaned_activity_in.get_vector().bit_or(orphaned_activity_out.get_vector()))

    # Reconnect the orphans. Only start/end edges are touched, so `reachability`
    # stays the closure of the activity-to-activity block either way.
    spdfg_without_orphans = spdfg.same_shape()
    spdfg_without_orphans[:] = spdfg[:]
    spdfg_without_orphans[start].assign_vector(
        from_start.bit_or(orphaned_activity_in.get_vector()))
    for i in range(numb_of_activities):
        spdfg_without_orphans[i][end] = to_end[i].bit_or(orphaned_activity_out[i])

    unconnected = sanitize_matrix_inverse(reachability + reachability.transpose())

    activity_concurrency = sint.Matrix(rows=numb_of_activities, columns=numb_of_activities)
    for i in range(numb_of_activities):
        activity_concurrency[i].assign_vector(
            concurrency_matrix[i].get_vector(base=0, size=numb_of_activities))

    # Recorded concurrency, read off the relation before it is rewritten below:
    # 1 for an activity the log ever showed interleaving with another one.
    recorded_concurrency = Array.create_from(
        sanitize_list(Array.create_from(
            activity_concurrency.mul(ones_column).get_vector())))
    completes = Array.create_from(
        orphaned_activity.get_vector().bit_and(recorded_concurrency.get_vector()))

    # 1 wherever at least one of the two endpoints is orphaned and has recorded
    # concurrency. Masking by `unconnected` below keeps the completion from
    # claiming parallelism that the repaired graph shows as a sequence.
    completed_pairs = sint.Matrix(rows=numb_of_activities, columns=numb_of_activities)
    completed_pairs.assign_all(1)
    completed_pairs = completed_pairs - mask_excluding_marked_row_columns(completes)
    for i in range(numb_of_activities):
        completed_pairs[i][i] = 0

    activity_concurrency = sanitize_matrix(
        (activity_concurrency + completed_pairs).schur(unconnected))

    sanitized_concurrency = concurrency_matrix.same_shape()
    sanitized_concurrency.assign_all(0)
    for i in range(numb_of_activities):
        sanitized_concurrency[i].assign_vector(activity_concurrency[i].get_vector())

    # The graph is revealed here, before the relation leaves this protocol, so
    # that the relation can be masked on the way out to the pairs a split can
    # read: split discovery compares two activities only where they leave a
    # common node, and (G^T G)[a,b] counts the nodes leading to both. Masking
    # after the relation is revealed would withhold nothing, and reading the
    # mask off the revealed graph makes it free -- a public product, a public
    # comparison, and a share times a public bit are all local.
    #
    # The slice is necessary: the graph spans all N nodes while the relation is
    # kept over the activities alone.
    revealed_spdfg = spdfg_without_orphans.reveal()
    co_successor = revealed_spdfg.transpose().mul(revealed_spdfg)
    for i in range(numb_of_activities):
        sanitized_concurrency[i].assign_vector(
            sanitized_concurrency[i].get_vector(base=0, size=numb_of_activities)
            * (co_successor[i].get_vector(base=0, size=numb_of_activities) > 0))

    return revealed_spdfg, sanitized_concurrency


def activity_to_organization_binning(sorted_events: Matrix,
                                     demuxed_activites: Matrix,
                                     numb_of_organizations: int,
                                     numb_of_activities: int) -> Matrix:
    with Timer("demux-orgs"):
        demuxed_orgs = DemuxKellerWithoutBitpacking.demux_batch(Array.create_from(sorted_events.get_column(3)), numb_of_organizations)
    with Timer("matmul"):
        truncated_demuxed_activities = sint.Matrix(len(demuxed_activites), numb_of_activities)

        @for_range(len(demuxed_activites))
        def trunacate_activities(i: cint) -> None:
            truncated_demuxed_activities[i][:] = demuxed_activites[i][:numb_of_activities]

        result = demuxed_orgs.transpose().mul(truncated_demuxed_activities)
    with Timer("normalize"):
        # print_ln("Activity_binning before normalization: %s", result.reveal())
        result[:] = (result[:] > 0)
    return result


def build_split_miner_artifacts(
        events: Matrix,
        number_of_event_types: int,
        numb_of_organizations: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        epsilon: Fraction,
        eta: Fraction,
        debug_prints: bool,
        validate_inputs: bool):
    n_events = events.shape[0]

    # Entered unconditionally so that the stage keeps its column in the runtime
    # tables (reporting 0 seconds) when the checks are compiled out.
    with Timer("input-validation"):
        if validate_inputs:
            validate_event_log(
                events=events,
                number_of_event_types=number_of_event_types,
                case_id_bitsize=case_id_bitsize,
                timestamp_bitsize=timestamp_bitsize
            )

    with Timer("dfg"):
        dfg, sorted_events, is_new_case, demuxed_events = build_directly_follows_graph_and_its_artifacts_fully_obliviously(
            events=events,
            number_of_event_types=number_of_event_types,
            case_id_bitsize=case_id_bitsize,
            timestamp_bitsize=timestamp_bitsize,
            debug_prints=debug_prints,
        )

        dfg: Matrix

    with Timer("self-loops"):
        self_loops = detect_self_loops(dfg=dfg)

    with Timer("short-loops"):
        short_loops_matrix = detect_short_loops(
            is_new_case=is_new_case,
            demuxed_events=demuxed_events,
            number_of_event_types=number_of_event_types,
            n_events=n_events,
        )

    with Timer("concurrency"):
        left_and_right_positive_dfg = detect_left_right_positive(
            dfg=dfg
        )

        concurrency_matrix = detect_concurrency(
            dfg=dfg,
            short_loop_matrix=short_loops_matrix,
            epsilon=epsilon,
            left_and_right_positive_dfg=left_and_right_positive_dfg,
            n_events=n_events,
        )

    with Timer("pruning"):
        pdfg = dfg_pruning(
            dfg=dfg,
            concurrency_matrix=concurrency_matrix,
            short_loop_matrix=short_loops_matrix,
            self_loops=self_loops
        )

    with Timer("reformat"):
        reformated_pdfg = reformat_start_and_end(pdfg=pdfg)
        dim = reformated_pdfg.shape[0]

    with Timer("capacity-edge-filter"):
        with Timer("forward"):
            best_incoming_edges = find_best_incoming_edges_dijkstra(
                pdfg=reformated_pdfg,
                source=dim - 1,
                n_events=n_events
            )

        with Timer("backwards"):
            best_outgoing_edges = find_best_outgoing_edges(
                pdfg=reformated_pdfg,
                sink=dim - 2,
                n_events=n_events
            )

        with Timer("demux"):
            incoming_matrix = DemuxKellerWithoutBitpacking.demux_batch(
                x_es=best_incoming_edges,
                n=dim,
                conds=Array.create_from(best_incoming_edges.get_vector().less_than(dim))
            ).transpose()

            outgoing_matrix = DemuxKellerWithoutBitpacking.demux_batch(
                x_es=best_outgoing_edges,
                n=dim,
                conds=Array.create_from(best_outgoing_edges.get_vector().less_than(dim))
            )

    with Timer("eta-filter"):
        eta_matrix = keep_eta_percentile_edges(
            pdfg=reformated_pdfg,
            eta=eta,
        )

    with Timer("filter-combine"):
        sfpdfg = eta_matrix.same_shape()
        sfpdfg.assign_vector(
            eta_matrix.get_vector().bit_or(incoming_matrix.get_vector()).bit_or(outgoing_matrix.get_vector())
        )

    with Timer("activity-binning"):
        activity_binning = activity_to_organization_binning(
            sorted_events=sorted_events,
            demuxed_activites=demuxed_events,
            numb_of_organizations=numb_of_organizations,
            numb_of_activities=number_of_event_types,
        )

    with Timer("concurrency-sanitization"):
        sfpdfg, concurrency_matrix = sanitize_conc_matrix(
            concurrency_matrix=concurrency_matrix,
            spdfg=sfpdfg,
            numb_of_activities=number_of_event_types,
        )

    return (sfpdfg, self_loops.reveal(),
            concurrency_matrix.reveal(), activity_binning.reveal())


@TimerContext("federated-pm", "split-miner")
def discover_bpmn(
        events: Matrix,
        number_of_event_types: int,
        numb_of_organizations: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        epsilon: Fraction,
        eta: Fraction,
        debug_prints: bool,
        activities: List[str],
        validate_inputs: bool):
    """End-to-end socket demo decomposed into independent reusable pieces:

    - Connection handling: :class:`MPSPDZSocketSession` (``socket_io.py``).
    - Computation: ``_compute_demo_values``.
    - Output / input: ``session.send_cints`` / ``session.read_cints``.

    Matching Python client: ``socket_program.py`` at the repository root.

    One-time TLS setup (run from ``/workspaces/rennert-smpc-miner/mp-spdz``)::

        ./Scripts/setup-clients.sh 1   # certificate for 1 client
    """
    from .socket_io import MPSPDZSocketSession

    PORT = 14000

    with MPSPDZSocketSession(port=PORT) as session:
        with Timer("client-init"):
            # Consume the client's handshake flag.
            session.read_regint()

        with Timer("smpc-computation", space_size=100):
            sfpdfg, self_loops, concurrencies, activity_binning = build_split_miner_artifacts(
                events=events,
                number_of_event_types=number_of_event_types,
                numb_of_organizations=numb_of_organizations,
                case_id_bitsize=case_id_bitsize,
                timestamp_bitsize=timestamp_bitsize,
                epsilon=epsilon,
                eta=eta,
                debug_prints=debug_prints,
                validate_inputs=validate_inputs,
            )

            # ----- I/O -----
            with Timer("output-to-client"):
                print_ln("Send revealed sfpdfg to client of size (%s, %s)", sfpdfg.shape[0], sfpdfg.shape[0])
                session.send_cints(sfpdfg.to_array())

                print_ln("Send self loops to client")
                session.send_cints(self_loops)

                print_ln("Send concurrencies to client")
                session.send_cints(concurrencies.to_array())

                print_ln("Send activities to client: %s", activities)
                session.send_string(json.dumps(activities))

                print_ln("Send acitivity binning to client: %s", activity_binning)
                session.send_cints(activity_binning.to_array())

        with Timer("client-local-computation"):
            print_ln("Waiting for client to finish computation.")
            session.read_regint()
            print_ln("   ... done")

    return sfpdfg
