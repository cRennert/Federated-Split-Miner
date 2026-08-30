

from dataclasses import dataclass
from typing import Any, Callable

from neonik.neon.computationreport import ComputationReport


@dataclass
class AdditionalRunParameters:
    custom_metadata: dict[str, Any] | None = None
    post_launch_hook: Callable[[], None] | None = None
    post_launch_hook_args: tuple[Any,...] | None = None

    post_process_report_hook: Callable[[ComputationReport], ComputationReport] | None = None