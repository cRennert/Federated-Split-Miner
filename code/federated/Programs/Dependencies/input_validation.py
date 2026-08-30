import math

from Compiler.types import Matrix, sint
from Compiler.library import *


def validate_event_log(events: Matrix,
                       number_of_event_types: int,
                       case_id_bitsize: int,
                       timestamp_bitsize: int) -> None:
    """Abort unless every event in ``events`` is one an honest client could submit.

    See the module docstring for why these are the checks that are needed, and why
    the timestamp and organization columns are not among them.
    """
    def rangecheck(column: sint, min_val: int | None, max_val: int | None):
        to_low = sint(0)
        if min_val is not None:
            to_low = column < min_val
        to_high = sint(0)
        if max_val is not None:
            to_high = column > max_val

        total = to_low.sum() + to_high.sum()
        runtime_error_if(total.reveal() != 0, "input validation failed.")

    rangecheck(events.get_column(0), 0, 2**case_id_bitsize-1)
    rangecheck(events.get_column(1), 0, number_of_event_types + 1)
    rangecheck(events.get_column(2), 0, 2**timestamp_bitsize-1)
