from typing import Callable

from Compiler.types import sint, Matrix, Array
from Compiler.library import *
from .case_id_encryption import MiMCCaseIdEncryption
from .utils.decorators import warn_unmergable
from .utils.timers import CompilationTimeWatch, TimerContext, Timer


@warn_unmergable(alternative=None)
@TimerContext("federated-pm", "log-merge")
def merge_logs_by_encrypt_reveal_and_quicksort(events: Matrix,
                                               case_id_bitsize: int,
                                               timestamp_bitsize: int,
                                               debug_prints: bool = False) -> tuple[Matrix, Array, Array]:
    def clear_quicksort(keys, data):
        """
        Sort `data` (sint.Matrix) in-place according to clear `keys` (cint.Array).
        Uses iterative quicksort with Lomuto partitioning — no recursion.
        `keys` length must match `data.shape[0]`.

        Vibe-coded using Claude Opus 4.6 in VS Code's agent mode.
        Subsequent prompts had to clarify that MP-SPDZ @for_range loops should be
        used where possible and that QuickSort should be implemented (rather than bubblesort).
        In the end, implementing by hand probably would have been just as fast.
        """
        n_rows = data.shape[0]
        n_cols = data.shape[1]
        if n_rows <= 1:
            return

        # explicit stack for (lo, hi) ranges
        stack_lo = cint.Array(n_rows)
        stack_hi = cint.Array(n_rows)
        sp = cint(0)

        stack_lo[0] = cint(0)
        stack_hi[0] = cint(n_rows - 1)
        sp.update(1)

        temp_row = sint.Array(n_cols)  # buffer to swap sint rows
        temp_key = cint(0)             # buffer to swap keys
        pi = cint(0)

        # bounded loop: in worst case we push O(n) ranges, so 2*n iterations suffice
        @for_range(2 * n_rows)
        def _(_):
            @if_(sp > 0)
            def _():
                sp.iadd(-1)
                lo = stack_lo[sp]
                hi = stack_hi[sp]

                @if_(lo < hi)
                def _():
                    pivot = keys[hi]
                    pi.update(lo)

                    @for_range(lo, hi)
                    def _(j):
                        @if_(keys[j] <= pivot)
                        def _():
                            # swap data rows at pi and j (sint)
                            @for_range(n_cols)
                            def _(c):
                                temp_row[c] = data[pi][c]
                                data[pi][c] = data[j][c]
                                data[j][c] = temp_row[c]

                            # swap keys at pi and j (cint)
                            temp_key = keys[pi]
                            keys[pi] = keys[j]
                            keys[j] = temp_key

                            pi.iadd(1)

                    # put pivot into position pi (swap data[pi] <-> data[hi], keys too)
                    @for_range(n_cols)
                    def _(c):
                        temp_row[c] = data[pi][c]
                        data[pi][c] = data[hi][c]
                        data[hi][c] = temp_row[c]

                    temp_key = keys[pi]
                    keys[pi] = keys[hi]
                    keys[hi] = temp_key

                    # push right and left subranges if they contain >= 2 elements
                    @if_(pi + 1 < hi)
                    def _():
                        stack_lo[sp] = pi + 1
                        stack_hi[sp] = hi
                        sp.iadd(1)

                    @if_(lo + 1 < pi)
                    def _():
                        stack_lo[sp] = lo
                        stack_hi[sp] = pi - 1
                        sp.iadd(1)

    def dynamic_count_comparisons_with_public_output(comparison_lhs: Array, comparison_rhs: Array, count: cint,
                                                     count_lower_bound: int = 0,
                                                     count_upper_bound: int | None = None,
                                                     is_good_bucket: Callable[[int, int], bool] | None = None) -> Array:
        assert len(comparison_lhs) == len(comparison_rhs)

        if count_upper_bound is None:
            count_upper_bound = len(comparison_lhs)
        if is_good_bucket is None:
            def is_good_bucket(lower, upper): return (upper <= lower * 1.1) or (upper <= lower + 1)

        result = cint.Array(count_upper_bound)
        result.assign_all(0)

        def compare_in_count_range(lower: int, upper: int):
            if is_good_bucket(lower, upper):
                result.assign_part_vector((comparison_lhs[:upper].less_equal(
                    comparison_rhs[:upper], bit_length=timestamp_bitsize)).reveal())
                return

            mid = (lower + upper) // 2

            @if_e(count <= mid)
            def comp_lower():
                compare_in_count_range(lower, mid)

            @else_
            def comp_upper():
                compare_in_count_range(mid + 1, upper)

        compare_in_count_range(count_lower_bound, count_upper_bound)
        return result

    def secret_batch_quicksort(data: Matrix, key_column: int, initial_parititons: Array):
        """
        Coded by hand.
        """

        n_elements = len(initial_parititons)
        parititions = Array.create_from(initial_parititons[:])

        iteration_count = cint(0)

        # Todo: better finish check.
        @while_do(lambda: sum(parititions) < n_elements)
        def iteration():
            # start_timer(31241)  # Just to see progress :)
            iteration_count.iadd(1)

            if debug_prints:
                print_ln("partitions=%s", parititions)

            comparison_lhs = sint.Array(n_elements)
            comparison_rhs = sint.Array(n_elements)
            comparison_count = cint(0)

            current_pivot = sint(-1)

            @for_range(n_elements)
            def prepare_comparison(i: cint) -> None:
                @if_e(parititions[i])
                def new_pivot():
                    current_pivot.update(data[i][key_column])

                @else_
                def new_comparison():
                    comparison_lhs[comparison_count] = current_pivot
                    comparison_rhs[comparison_count] = data[i][key_column]
                    comparison_count.iadd(1)

            if debug_prints:
                print_ln("Comparison: lhs=%s", comparison_lhs.reveal())
                print_ln("Comparison: rhs=%s", comparison_rhs.reveal())

            comparison_results = dynamic_count_comparisons_with_public_output(comparison_lhs, comparison_rhs, comparison_count)

            if debug_prints:
                print_ln("Comparison: res=%s", comparison_results.reveal())
                print_ln("comparison_count=%s", comparison_count)

            new_partitions = Array.create_from(parititions[:])
            new_data = data.same_shape()

            current_partition_start = cint(0)
            current_comparison_pos = cint(0)

            @while_do(lambda: current_partition_start < n_elements)
            def create_new_paritions():
                if debug_prints:
                    print_ln("current_partition_start=%s", current_partition_start)
                    print_ln("parititions[current_partition_start]=%s", parititions[current_partition_start])

                @if_e(parititions[current_partition_start])
                def seperate_current_partition():
                    second_pass_comparison_pos = cint(current_comparison_pos)

                    next_partition_start = cint(current_partition_start + 1)
                    below_pivot_count = cint(0)

                    # mod n_elements is needed to avoid an index out of bounds for the last partition.
                    @while_do(lambda: (next_partition_start < n_elements) * (1 - parititions[next_partition_start % n_elements]))
                    def count_below_pivot():
                        below_pivot_count.iadd(1 - comparison_results[current_comparison_pos])
                        current_comparison_pos.iadd(1)
                        next_partition_start.iadd(1)

                    if debug_prints:
                        print_ln("partition %s: next_partition_start=%s below_pivot_count=%s",
                                 current_partition_start, next_partition_start, below_pivot_count)

                    paritition_positions = Array.create_from([current_partition_start, current_partition_start + below_pivot_count])

                    new_data[paritition_positions[1]][:] = data[current_partition_start][:]
                    paritition_positions[1] += 1
                    new_partitions[current_partition_start + below_pivot_count] = 1

                    @if_((below_pivot_count == 0) * (current_partition_start + 1 < n_elements))
                    def worst_case_scenario():
                        new_partitions[current_partition_start + 1] = 1

                    @for_range(current_partition_start + 1, next_partition_start)
                    def move_data_to_new_partitions(i: cint) -> None:
                        if debug_prints:
                            print_ln("partition %s: paritition_positions=%s second_pass_comparison_pos=%s",
                                     current_partition_start, paritition_positions, second_pass_comparison_pos)
                        subpartition = comparison_results[second_pass_comparison_pos]
                        second_pass_comparison_pos.iadd(1)

                        new_data[paritition_positions[subpartition]][:] = data[i][:]
                        paritition_positions[subpartition] += 1

                    current_partition_start.update(next_partition_start)
                    if debug_prints:
                        print_ln("iteration done")

                @else_
                def move_to_next_partition():
                    current_partition_start.iadd(1)

            data[:] = new_data[:]
            parititions[:] = new_partitions[:]
            # stop_timer(31241)

            @if_((iteration_count % 100) == 0)
            def print_progress():
                print_ln(f"Timestamp sort progress: %s / {n_elements}", sum(parititions))

        print_ln("Timestamp quicksort required %s iterations.", iteration_count)

    with Timer("reveal-and-quicksort"):
        with Timer("Prep"):
            n_events = len(events)
            events_copy = events.same_shape()
            events_copy[:] = events[:]

            events_copy.secure_shuffle()

        with Timer("caseid-encryption"):
            encrypted_case_ids = MiMCCaseIdEncryption.encrypt_case_ids_with_random_key(
                Array.create_from(events_copy.get_column(0)),
                case_id_bitsize=case_id_bitsize,
                debug_prints=debug_prints
            ).reveal()

        if debug_prints:
            print_ln("shuffled and encrypted event log:")
            for i, row in enumerate(events_copy.reveal()):
                print_ln("%s %s", encrypted_case_ids[i], row.reveal())

        with Timer("caseid-sort"):
            clear_quicksort(encrypted_case_ids, events_copy)

        if debug_prints:
            print_ln("sorted by case id:")
            for i, row in enumerate(events_copy.reveal()):
                print_ln("%s %s", encrypted_case_ids[i], row.reveal())

        with Timer("caseid-boundaries"):
            is_new_case = cint.Array(n_events)
            is_new_case[0] = 1
            is_new_case.assign_part_vector(encrypted_case_ids[0:n_events-1] != encrypted_case_ids[1:], base=1)

        if debug_prints:
            print_ln("is_new_case=%s", is_new_case)

        with Timer("timestamp-sort"):
            secret_batch_quicksort(events_copy, 2, is_new_case)
        return events_copy, is_new_case, encrypted_case_ids


@warn_unmergable(alternative=None)
@TimerContext("federated-pm", "log-merge")
def merge_logs_by_oblivious_radix_sort(events: Matrix,
                                       case_id_bitsize: int,
                                       timestamp_bitsize: int,
                                       debug_prints: bool = False) -> Matrix:
    with Timer("radix-sort"):
        events_copy: Matrix = events.same_shape()  # type: ignore
        events_copy[:] = events[:]
        # Sort by timestamp first.
        with Timer("sort-by-timestamp"):
            events_copy.sort(key_indices=(2, ), n_bits=timestamp_bitsize, batcher=False)
        # And then by case id. Since Radix is stable, each case will be sorted by
        # timestamp.
        with Timer("sort-by-caseid"):
            events_copy.sort(key_indices=(0, ), n_bits=case_id_bitsize, batcher=False)
        return events_copy
