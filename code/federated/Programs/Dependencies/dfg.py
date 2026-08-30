from Compiler.types import MultiArray, sint, Matrix, Array
from Compiler.library import *
from .log_merge import merge_logs_by_encrypt_reveal_and_quicksort, merge_logs_by_oblivious_radix_sort
from .utils.timers import CompilationTimeWatch, Timer
from .demux import DemuxKellerWithoutBitpacking


def build_left_right_directly_follows_graph(
        events: Matrix,
        number_of_event_types: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        debug_prints: bool,
) -> Matrix:
    """
    Builds a directly follows graph with secret output.

    O(case_id_bitsize + timestamp_bitsize) rounds.

    :input events: A table with the following columns (case_id, event, timestamp)

    :returns: A secret matrix corresponding to the directly-follows-graph.
    """
    n_events = events.shape[0]
    n_activities = number_of_event_types + 1

    with Timer("log-merge"):
        events_copy, is_new_event, encrypted_case_ids = merge_logs_by_encrypt_reveal_and_quicksort(
            events, case_id_bitsize, timestamp_bitsize, debug_prints=debug_prints)
    activities = sint.Array(n_events)
    activities.assign(events_copy.get_column(1))

    # Edge i = transition (event i -> event i+1). It is a "false" between-case edge
    # iff event i+1 starts a new case.
    transition_is_false = is_new_event.get_part_vector(base=1, size=n_events - 1)
    offset = n_activities * transition_is_false
    left = activities.get_vector(base=0, size=n_events - 1)
    right = activities.get_vector(base=1, size=n_events - 1)

    # Encode sources / destinations so false-edge values land in columns [n_activities, 2*n_activities)
    # of the demux output, letting us separately count normal edges, case ends, and case starts.
    edge_sources = sint.Array(n_events - 1)
    edge_sources.assign(left + offset)
    edge_destinations = sint.Array(n_events - 1)
    edge_destinations.assign(right + offset)

    edge_sources_demux = DemuxKellerWithoutBitpacking.demux_batch(edge_sources, 2 * n_activities)
    edge_destinations_demux = DemuxKellerWithoutBitpacking.demux_batch(edge_destinations, 2 * n_activities)

    result = sint.Matrix(n_activities, n_activities)
    result.assign_all(0)

    bottom = number_of_event_types  # row/column index aliased with the highest activity, matching the native convention.

    @for_range(n_activities)
    def _(i: cint) -> None:
        result[i][bottom] += edge_sources_demux.get_column(i + n_activities).sum()
        result[bottom][i] += edge_destinations_demux.get_column(i + n_activities).sum()

        @for_range(n_activities)
        def _(j: cint) -> None:
            result[i][j] += (edge_sources_demux.get_column(i) * edge_destinations_demux.get_column(j)).sum()

    # The transition loop above misses the first event's case start and the last event's case end.
    first_act_demux = DemuxKellerWithoutBitpacking.demux(activities[0], n_activities)
    last_act_demux = DemuxKellerWithoutBitpacking.demux(activities[n_events - 1], n_activities)

    @for_range(n_activities)
    def _(i: cint) -> None:
        result[bottom][i] += first_act_demux[i]
        result[i][bottom] += last_act_demux[i]

    return result


def build_clear_directly_follows_graph(
        events: Matrix,
        number_of_event_types: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        debug_prints: bool,
) -> Matrix:
    """
    Builds a directly follows graph with public output.

    O(case_id_bitsize + timestamp_bitsize) rounds, O()

    :input events: A table with the following columns (case_id, event, timestamp)

    :returns: A clear matrix corresponding to the directly-follows-graph.
    """
    n_events = events.shape[0]

    events_copy = events.same_shape()
    events_copy[:] = events[:]

    with CompilationTimeWatch("Sort and checking"):
        start_timer(31)
        events_copy, is_new_event, _ = merge_logs_by_encrypt_reveal_and_quicksort(
            events, case_id_bitsize, timestamp_bitsize, debug_prints=debug_prints)
        stop_timer(31)

    is_new_event = is_new_event.get_part_vector(base=1, size=n_events - 1)

    edge_sources = Array.create_from(
        is_new_event.if_else(-1 - events_copy.get_column(1).get_vector(base=0, size=n_events - 1),
                             events_copy.get_column(1).get_vector(base=0, size=n_events - 1))
    )
    edge_destinations = Array.create_from(
        is_new_event.if_else(-1 - events_copy.get_column(1).get_vector(base=1, size=n_events - 1),
                             events_copy.get_column(1).get_vector(base=1, size=n_events - 1))
    )

    # Step 3: Build the directly follows matrix by first shuffling and then revealing the edges.
    # O(1) rounds, O(n_events) comm, O(n_events) comp.
    # When using permutation nets instead of native permutations, all complexities are multiplied by log(n_events).
    permutation = sint.get_secure_shuffle(n_events - 1)
    edge_sources.secure_permute(permutation)
    edge_destinations.secure_permute(permutation)

    edge_sources = edge_sources.reveal()
    edge_destinations = edge_destinations.reveal()

    result = cint.Matrix(number_of_event_types + 1, number_of_event_types + 1)
    result.assign_all(0)

    @for_range(n_events - 1)
    def iter(i: cint) -> None:
        @if_e(edge_sources[i] >= 0)
        def _() -> None:
            result[edge_sources[i]][edge_destinations[i]] += 1

        @else_
        def _() -> None:
            result[- (edge_sources[i] + 1)][number_of_event_types] += 1
            result[number_of_event_types][- (edge_destinations[i] + 1)] += 1

    result[number_of_event_types][number_of_event_types] -= 1

    return result


def build_directly_follows_graph_and_its_artifacts_by_leaking_case_length_distribution(
        events: Matrix,
        number_of_event_types: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        debug_prints: bool,
) -> tuple[Matrix | MultiArray, Matrix, Array, Matrix]:
    """
    Builds a directly follows graph with public output.

    O(case_id_bitsize + timestamp_bitsize) rounds, O()

    :input events: A table with the following columns (case_id, event, timestamp)

    :returns: A clear matrix corresponding to the directly-follows-graph.
    """
    n_events = events.shape[0]

    with Timer("log-merge"):
        events_copy, is_new_event, encrypted_case_ids = merge_logs_by_encrypt_reveal_and_quicksort(
            events, case_id_bitsize, timestamp_bitsize, debug_prints=debug_prints)

    # TODO: This can surely be improved to a single demux pass and
    # a copy passes, at least when is_new_event is public.
    with Timer("demux"):
        n_activities = number_of_event_types + 1
        bottoms = sint.Array(n_events)
        bottoms.assign(sint(n_activities - 1, size=n_events))
        bottom_demux = DemuxKellerWithoutBitpacking.demux_batch(bottoms, n_activities, is_new_event)

        start_bottom_demux = sint.Matrix(rows=n_events, columns=n_activities)
        start_bottom_demux.assign_part_vector(bottom_demux.get_vector(), base=0)

        end_bottom_demux = sint.Matrix(rows=n_events, columns=n_activities)
        end_bottom_demux.assign_part_vector(bottom_demux.get_part_vector(base=1, size=n_events-1), base=0)
        end_bottom_demux[n_events - 1][n_activities - 1] = sint(1)

        act_col = sint.Array(n_events)
        act_col.assign(events_copy.get_column(1))
        demuxed_events = DemuxKellerWithoutBitpacking.demux_batch(act_col, n_activities)

        is_no_new_event = Array.create_from(cint(val=1, size=n_events) - is_new_event)
        masked_demuxed_events = DemuxKellerWithoutBitpacking.demux_batch(act_col, n_activities, is_no_new_event)

    with Timer("Matrix-Mul"):
        dfr_within_case = demuxed_events.get_part(
            start=0, size=n_events-1).trans_mul(masked_demuxed_events.get_part(start=1, size=n_events-1))
        dfr_start = start_bottom_demux.trans_mul(demuxed_events)
        dfr_end = demuxed_events.trans_mul(end_bottom_demux)

        dfg = dfr_within_case + dfr_start + dfr_end

    return dfg, events_copy, encrypted_case_ids, demuxed_events


def build_directly_follows_graph(
        events: Matrix,
        number_of_event_types: int,
        case_id_bitsize: int,
        timestamp_bitsize: int,
        debug_prints: bool,
) -> Matrix | MultiArray:
    dfg, _, _, _ = build_directly_follows_graph_and_its_artifacts_by_leaking_case_length_distribution(
        events=events,
        number_of_event_types=number_of_event_types,
        case_id_bitsize=case_id_bitsize,
        timestamp_bitsize=timestamp_bitsize,
        debug_prints=debug_prints,
    )
    return dfg


def build_directly_follows_graph_and_its_artifacts_fully_obliviously(events: Matrix,
                                                                     number_of_event_types: int,
                                                                     case_id_bitsize: int,
                                                                     timestamp_bitsize: int,
                                                                     debug_prints: bool,) -> tuple[Matrix, Matrix, Array, Matrix]:
    """
    Builds a directly follows graph with secret output, without leaking anything about the
    input log -- in particular not the case length distribution that
    :func:`build_directly_follows_graph_and_its_artifacts_by_leaking_case_length_distribution`
    exposes by revealing (encrypted) case ids and sorting them in the clear.

    :input events: A table with the following columns (case_id, event, timestamp)

    :returns: The secret DFG, the sorted event log, ``None`` (the leaking variant returns the
        revealed case ids here, which do not exist in this variant), and the demultiplexed
        activities of the sorted log.
    """
    n_events = events.shape[0]
    n_activities = number_of_event_types + 1
    bottom = n_activities - 1  # Row/column of the artificial start/end activity, matching the native convention.

    with Timer("log-merge"):
        events = merge_logs_by_oblivious_radix_sort(
            events, case_id_bitsize, timestamp_bitsize, debug_prints=debug_prints)

    with Timer("demux"):
        demuxed_events = DemuxKellerWithoutBitpacking.demux_batch(Array.create_from(events.get_column(1)), n_activities)

    with Timer("matmul-prep"):
        case_ids = Array.create_from(events.get_column(0))
        is_new_case = sint.Array(n_events)
        is_new_case[0] = sint(1)
        # Whenever the case ID changes, a new case starts. This may not only be growing, based
        # on the sorting algorithm, e.g., ``radix_sort`` with ``signed=True``.
        is_new_case[1:] = 1 - case_ids[1:].equal(case_ids[0:n_events - 1])

        successors_within_case = sint.Matrix(n_events - 1, n_activities)
        successors_within_case[:] = demuxed_events.get_part_vector(base=1, size=n_events - 1) * \
            (1 - is_new_case[regint.inc((n_events - 1) * n_activities, 1, 1, n_activities)])

        is_case_end = sint.Array(n_events)
        is_case_end[0:n_events - 1] = is_new_case.get_part_vector(base=1, size=n_events - 1)
        is_case_end[n_events - 1] = sint(1)

        case_boundaries = sint.Matrix(n_events, 2)
        case_boundaries.set_column(0, is_new_case[:])
        case_boundaries.set_column(1, is_case_end[:])

    with Timer("matmul"):
        dfg = demuxed_events.get_part(start=0, size=n_events - 1).trans_mul(successors_within_case)

        # Row 0 counts how often each activity starts a case, row 1 how often it ends one.
        boundary_activities = case_boundaries.trans_mul(demuxed_events)
        dfg[bottom][:] = dfg[bottom][:] + boundary_activities[0][:]
        dfg.set_column(bottom, dfg.get_column(bottom) + boundary_activities[1][:])

    return dfg, events, is_new_case, demuxed_events
