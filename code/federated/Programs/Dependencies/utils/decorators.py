from functools import wraps
from typing import Callable

import Compiler.papers
from Compiler.library import get_program

Compiler.papers.papers.update({
    # Keller et al., Faster Secure Multi-party Computation of AES and DES Using Lookup Tables
    "KORS+17": "https://doi.org/10.1007/978-3-319-61204-1_12",
    # Launchburry et al., Efficient lookup-table protocol in secure multiparty computation
    "LDDA12": "https://dl.acm.org/doi/10.1145/2364527.2364556",
    # Anagreh et al., Parallel Privacy-Preserving Shortest Path Algorithms
    "ALV21": "https://doi.org/10.3390/cryptography5040027"
})


def recommended_reading(category: str, paper: str):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            get_program().reading(category, paper)
            return func(*args, **kwargs)
        return wrapped
    return decorator


FUNCTION_USE_COUNTS = {}


def warn_unmergable(alternative: str | None = None):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            global FUNCTION_USE_COUNTS
            FUNCTION_USE_COUNTS[func] = FUNCTION_USE_COUNTS.get(func, 0) + 1
            if FUNCTION_USE_COUNTS[func] == 2:
                alt_message = f" You can use \"{alternative}\" if you need a merged implementation." if alternative is not None else ""
                print(f"Warning: \"{func.__qualname__}\" is known to be unmergable, i.e., the compiler cannot merge multiple function calls. This might affect the performance of your protocol.{alt_message}")
            # If you are getting a "TypeError: classmethod is not callable" pointing here,
            # make sure that the @warn_unmergable() is used AFTER the @classfunction.
            return func(*args, **kwargs)
        wrapped.__unmergable__ = True
        return wrapped
    return decorator


def is_unmergable(func: Callable) -> bool:
    return getattr(func, "__unmergable__", False)
