from functools import wraps
import time
from typing import Any, Iterable


DEFAULT_TIMER_SPACE_SIZE: int = 10


def __primary_timer_id(i: int) -> int:
    return 10 + i


PRIMARY_TIMERS = {
    ("eval",): (__primary_timer_id(1), 10),
    ("utils",): (__primary_timer_id(2), 100),
    ("graph",): (__primary_timer_id(3), 100),
    ("federated-pm",): (__primary_timer_id(4), 100)
}


def __reserve_default_timers() -> None:
    Timer.reserve_subtimer_id(("eval", "init"), 1)
    Timer.reserve_subtimer_id(("eval", "loop-pre"), 2)
    Timer.reserve_subtimer_id(("eval", "main"), 3)
    Timer.reserve_subtimer_id(("eval", "loop-post"), 4)

    Timer.reserve_subtimer_id(("utils", "demux"), 1)

    Timer.reserve_subtimer_id(("graph", "successor-calculation"), 1)
    Timer.reserve_subtimer_id(("graph", "successor-calculation", "floyd-warshall"), 1)
    Timer.reserve_subtimer_id(("graph", "successor-calculation", "dijsktra"), 2)
    Timer.reserve_subtimer_id(("graph", "single-pair shortest-path"), 2)
    Timer.reserve_subtimer_id(("graph", "single-pair shortest-path", "successor-calculation"), 1)
    Timer.reserve_subtimer_id(("graph", "single-pair shortest-path", "path-construction"), 2)
    Timer.reserve_subtimer_id(("graph", "single-pair shortest-path", "complete-adaptive-path-construction"), 8)
    Timer.reserve_subtimer_id(("graph", "single-pair shortest-path", "complete-baseline"), 9)

    Timer.reserve_subtimer_id(("federated-pm", "log-merge"), 1)
    Timer.reserve_subtimer_id(("federated-pm", "log-merge", "reveal-and-quicksort"), 1)
    Timer.reserve_subtimer_id(("federated-pm", "log-merge", "radix-sort"), 2)
    Timer.reserve_subtimer_id(("federated-pm", "dfg"), 2)
    Timer.reserve_subtimer_id(("federated-pm", "split-miner"), 3)
    Timer.reserve_subtimer_id(("federated-pm", "split-miner", "client-init"), 1)
    Timer.reserve_subtimer_id(("federated-pm", "split-miner", "smpc-computation"), 2, space_size=100)
    Timer.reserve_subtimer_id(("federated-pm", "split-miner", "client-local-computation"), 3)

    split_miner_stages = ("federated-pm", "split-miner", "smpc-computation")
    for stage_id, stage in enumerate([
        "dfg",
        "self-loops",
        "short-loops",
        "concurrency",
        "pruning",
        "reformat",
        "capacity-edge-filter",
        "eta-filter",
        "filter-combine",
        "activity-binning",
        "concurrency-sanitization",
        "output-to-client",
        "input-validation",
    ], start=1):
        Timer.reserve_subtimer_id(split_miner_stages + (stage, ), stage_id)


class CompilationTimeWatch:
    timer_stack: list["CompilationTimeWatch"] = []

    parent: "CompilationTimeWatch | None"
    name: str
    start: float

    def __init__(self, name: str, parent: "CompilationTimeWatch | None" = None) -> None:
        self.name = name
        if parent is None and len(CompilationTimeWatch.timer_stack) > 0:
            parent = CompilationTimeWatch.timer_stack[-1]
        self.parent = parent

    def print(self, message: str, recursing: bool = False) -> None:
        if recursing:
            message = f"[{self.name}] {message}"

        if self.parent:
            self.parent.print(message, recursing=True)
        else:
            print(message, flush=True)

    def start_timer(self):
        self.start = time.time()
        CompilationTimeWatch.timer_stack.append(self)
        self.print(f"Compiling {self.name}...")

    def stop_timer(self):
        self.print(f"... done compiling {self.name}, took {time.time() - self.start:.2f} seconds")
        assert CompilationTimeWatch.timer_stack[-1] == self
        CompilationTimeWatch.timer_stack.pop()

    def __enter__(self):
        self.start_timer()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_timer()


class TimersEnabled:
    timers_enabled: bool = False

    _should_enable: bool

    def __init__(self, timers_enabled: bool = True) -> None:
        self._should_enable = timers_enabled

    def __enter__(self):
        self.old_timers_enabled = TimersEnabled.timers_enabled
        TimersEnabled.timers_enabled = self._should_enable

    def __exit__(self, exc_type, exc_val, exc_tb):
        TimersEnabled.timers_enabled = self.old_timers_enabled


class Timer:
    timer_stack: list["Timer"] = []
    id_and_space_size_by_timer: dict[tuple[str, ...], tuple[int, int]] = PRIMARY_TIMERS
    timer_by_id: dict[int, tuple[str, ...]] = {}
    timer_counter: int = 1

    name: str
    full_name: tuple[str, ...]
    timer_id: int
    timer_started: bool
    compilation_timer: CompilationTimeWatch | None = None

    @classmethod
    def reserve_timer_id(cls, full_name: tuple[str, ...] | str, timer_id: int,
                         space_size: int = DEFAULT_TIMER_SPACE_SIZE) -> None:
        if isinstance(full_name, str):
            full_name = (full_name, )

        if timer_id in cls.timer_by_id and cls.timer_by_id[timer_id] != full_name:
            raise ValueError(f"Timer {timer_id} already in use by timer \"{cls.timer_by_id[timer_id]}\".")
        cls.timer_by_id[timer_id] = full_name
        cls.id_and_space_size_by_timer[full_name] = (timer_id, space_size)

    @classmethod
    def reserve_subtimer_id(cls, full_name: tuple[str, ...], sub_timer_id: int, space_size: int = DEFAULT_TIMER_SPACE_SIZE) -> None:
        parent = tuple(full_name[:-1])
        assert parent in cls.id_and_space_size_by_timer, "Parent was not reservered."
        parent_timer, parent_space = cls.id_and_space_size_by_timer[parent]

        assert sub_timer_id < parent_space
        timer = parent_timer * parent_space + sub_timer_id
        assert timer not in cls.timer_by_id, "Timer id already occupied."

        cls.timer_by_id[timer] = full_name
        cls.id_and_space_size_by_timer[full_name] = (timer, space_size)

    @classmethod
    def pretty_print_timer_ids(cls):
        root = PrettyPrintNode("")
        for (name, (timer_id, space_size)) in cls.id_and_space_size_by_timer.items():
            root.add_child(name, timer_id, space_size)
        root.pretty_print()
        # items = list(sorted(cls.timer_by_id.items(), key=lambda x: x[0]))
        # for timer_id, full_name in items:
        #     print("  " * (len(full_name) - 1) + f"{full_name[-1]}: {timer_id}")
        #     try:
        #         from Compiler.library import print_ln
        #         print_ln(" ".join(full_name) + f": {timer_id}")  # MP-SPDZ will stip leading whitespaces :(
        #     except:
        #         pass

    @classmethod
    def determine_timer_id(cls, full_name: tuple[str, ...], parent_timer_space_size: int = DEFAULT_TIMER_SPACE_SIZE) -> int:
        def determine_recursively(name: tuple[str, ...], fallback_space_size: int = DEFAULT_TIMER_SPACE_SIZE) -> tuple[int, int]:
            if name in cls.id_and_space_size_by_timer:
                return cls.id_and_space_size_by_timer[name]

            if len(name) == 1:
                while Timer.timer_counter in cls.timer_by_id:
                    Timer.timer_counter += 1
                return (Timer.timer_counter, fallback_space_size)

            parent_timer, parent_space_size = determine_recursively(tuple(name[:-1]), fallback_space_size=fallback_space_size)
            for i in range(1, parent_space_size):
                timer = parent_timer * parent_space_size + i
                if timer not in cls.timer_by_id:
                    return (timer, fallback_space_size)
            raise TimerSpaceFullError(f"Timer space of {name} is full.")
        return determine_recursively(full_name, parent_timer_space_size)[0]

    def __init__(self, name: str, timer_id: int | None = None,
                 space_size: int = DEFAULT_TIMER_SPACE_SIZE,
                 compile_time_watch: bool = True) -> None:
        self.name = name
        self.full_name = tuple([timer.name for timer in Timer.timer_stack] + [self.name])
        self.timer_started = False

        if timer_id is None:
            timer_id = Timer.determine_timer_id(self.full_name)
        self.timer_id = timer_id
        Timer.reserve_timer_id(self.full_name, self.timer_id, space_size=space_size)

        if compile_time_watch:
            self.compilation_timer = CompilationTimeWatch(name)

    def __enter__(self):
        from Compiler.library import start_timer
        if TimersEnabled.timers_enabled:
            start_timer(self.timer_id)
            if self.compilation_timer is not None:
                self.compilation_timer.start_timer()

            self.timer_started = True
        Timer.timer_stack.append(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        from Compiler.library import stop_timer
        if self.timer_started:
            stop_timer(self.timer_id)
            if self.compilation_timer is not None:
                self.compilation_timer.stop_timer()
            self.timer_started = False
        Timer.timer_stack.pop()


class TimerContext:
    runtime_context: tuple[str, ...]
    overwrite_runtime_timers: bool
    append_compile_timers: bool

    prior_timer_stack: list[Timer] | None
    __overwritten_runtime_timer_stack: list[Timer] | None

    def __init__(self, *args,
                 overwrite_runtime_timers: bool = True,
                 append_compile_timers: bool = True) -> None:

        def flatten(x, res: list[Any] | None = None) -> list[Any]:
            if res is None:
                res = []
            if isinstance(x, str):
                res.append(x)
            else:
                for y in x:
                    flatten(y, res=res)
            return res
        self.runtime_context = tuple(flatten(args))
        self.overwrite_runtime_timers = overwrite_runtime_timers
        self.append_compile_timers = append_compile_timers

        self.prior_context = None
        self.__overwritten_runtime_timer_stack = None

    def __enter__(self):
        if self.overwrite_runtime_timers:
            self.prior_timer_stack = Timer.timer_stack
            if self.__overwritten_runtime_timer_stack is None:
                Timer.timer_stack = []
                for name in self.runtime_context:
                    Timer.timer_stack.append(Timer(name))
                self.__overwritten_runtime_timer_stack = Timer.timer_stack
            Timer.timer_stack = self.__overwritten_runtime_timer_stack
        if self.append_compile_timers:
            for name in self.runtime_context:
                CompilationTimeWatch.timer_stack.append(CompilationTimeWatch(name))

    def __exit__(self, exc_type, exc, tb):
        if self.overwrite_runtime_timers:
            assert self.prior_timer_stack is not None
            Timer.timer_stack = self.prior_timer_stack
        if self.append_compile_timers:
            for name in reversed(self.runtime_context):
                assert len(CompilationTimeWatch.timer_stack) > 0 and CompilationTimeWatch.timer_stack[-1].name == name
                CompilationTimeWatch.timer_stack.pop()

    def __call__(self, *annotator_args: Any, **kwds: Any) -> Any:
        """For use as annotation"""
        func = annotator_args[0]

        @wraps(func)
        def wrapped(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return wrapped


class TimerSpaceFullError(RuntimeError):
    pass


# We need to initialize the class first :)
__reserve_default_timers()


class PrettyPrintNode:
    name: str
    children: dict[str, "PrettyPrintNode"]
    timer: int | None
    space_size: int | None

    def __init__(self, name: str) -> None:
        self.name = name
        self.children = {}
        self.timer = None
        self.space_size = None

    def add_child(self, name: tuple[str, ...], timer: int, space_size: int):
        if len(name) == 0:
            self.timer = timer
            self.space_size = space_size
            return

        if name[0] not in self.children:
            self.children[name] = PrettyPrintNode(name[0])
        self.children[name].add_child(name[1:], timer, space_size)

    def min_timer_id(self) -> int | None:
        timers = []
        if self.timer is not None:
            timers.append(self.timer)
        for child in self.children.values():
            timers.append(child.min_timer_id())
        if len(timers) == 0:
            return None
        return min(timers)

    def pretty_print(self, prev_names: str = ""):
        curr_node_name = f"[{self.name}]" if len(self.name) > 0 else ""
        if len(prev_names) > 0:
            curr_node_name = f"{prev_names} {curr_node_name}"

        if self.timer is not None:
            print(f"{curr_node_name}: {self.timer}")
            try:
                from Compiler.library import print_ln
                print_ln(f"{curr_node_name}: {self.timer}")
            except:
                pass

        def sort_key(child: PrettyPrintNode):
            if self.timer is not None and self.space_size is not None:
                x = child.min_timer_id()
                if x is None:
                    return 999999999999999
                child_space_size = x // (self.timer * self.space_size)
                return x // child_space_size
            return child.min_timer_id()

        children = sorted(self.children.values(), key=sort_key)
        for child in children:
            child.pretty_print(curr_node_name)
