import base64
import json
import os
import shutil
import subprocess
from base64 import b64encode, b64decode
from collections import UserList
from typing import Dict, Optional, List, Iterable, Union, Callable, Any, Hashable, Tuple
import zlib
import requests

from .helper import average_and_standard_deviation, min_median_max, get_cdir, join_path_abs, NeonException, \
    get_workdir_or_default_workdir
from .network import Network
from .operationmode import OperationMode


class ComputationReport:
    """
    The computation report is a read only report on one single MPC computation.
    It contains all NEON-Parameters as well the outputs of the computation, most of which is
    stored in the client computation reports.

    It also provides utility functions for evaluating the reported timer times. All timer functions support named
    timers, i.e. you can also provide the name of a timer to the timer parameter.
    """

    # Parameters
    __program: str
    __pre_substitution_hash: str
    __substitutions: Dict[str, str]
    __program_hash: str
    __custom_version: str | None = None
    __filename: Optional[str] = None

    __timer_names: Dict[int, str]
    __protocol: str
    __operation_mode: OperationMode
    __delay: Optional[str] = None
    __outgoing_bandwidth: Optional[str] = None
    __incoming_bandwidth: Optional[str] = None
    __bits_from_squares: bool
    __batch_size: int
    __bucket_size: int
    __mpspdz_version: str

    __client_reports: List["ClientComputationReport"]

    __secrets_before_computation: Optional[List[int]] = None
    __secrets_after_computation: Optional[List[int]] = None

    __custom_metadata: Optional[Any] = None
    __max_cpu_usage: Optional[float] = None
    __max_memory_usage: Optional[int] = None
    __experiment_spec: dict[str, Any] | None = None

    def __init__(self,
                 program: str,
                 pre_substitution_hash: str,
                 substitutions: Dict[str, str],
                 program_hash: str,
                 timer_names: Dict[int, str],
                 protocol: str,
                 operation_mode: OperationMode,
                 delay: Optional[str],
                 outgoing_bandwidth: Optional[str],
                 incoming_bandwidth: Optional[str],
                 bits_from_squares: bool,
                 batch_size: int,
                 bucket_size: int,
                 mpspdz_version: str,
                 client_reports: List["ClientComputationReport"],
                 secret_before_computation: Optional[List[int]],
                 secrets_after_computation: Optional[List[int]],
                 custom_metadata: Optional[Any],
                 max_cpu_usage: float | None = None,
                 max_memory_usage: int | None = None,
                 experiment_spec: dict[str, Any] | None = None,
                 custom_version: str | None = None):
        self.__program = program
        self.__pre_substitution_hash = pre_substitution_hash
        self.__substitutions = substitutions
        self.__program_hash = program_hash
        self.__timer_names = timer_names
        self.__protocol = protocol
        self.__operation_mode = operation_mode
        self.__delay = delay
        self.__outgoing_bandwidth = outgoing_bandwidth
        self.__incoming_bandwidth = incoming_bandwidth
        self.__bits_from_squares = bits_from_squares
        self.__batch_size = batch_size
        self.__bucket_size = bucket_size
        self.__mpspdz_version = mpspdz_version
        self.__client_reports = client_reports
        self.__secrets_before_computation = secret_before_computation
        self.__secrets_after_computation = secrets_after_computation
        self.__custom_metadata = custom_metadata
        self.__max_cpu_usage = max_cpu_usage
        self.__max_memory_usage = max_memory_usage
        self.__experiment_spec = experiment_spec
        self.__custom_version = custom_version

    # region Timers
    def get_timer_keys(self) -> List[int]:
        return list(self.__client_reports[0].timers.keys())

    def timer_key_from_name(self, name: str) -> Optional[int]:
        for (key, timer_name) in self.__timer_names.items():
            if name == timer_name:
                return key
        return None

    def get_timer_id(self, timer: Union[int, str]) -> int:
        """Returns the integer id of the given timer. Raises an exception if the timer name is not known."""
        if isinstance(timer, str):
            result = self.timer_key_from_name(timer)
            if result is None:
                raise NeonException("Unknown timer name.")
            return result
        else:
            return timer

    def get_timer_times(self, timer: Union[int, str]) -> List[float]:
        """Returns the reported timer times from each client. If the timer is a string, the corresponding timer wil be
        looked up. Raises an exception if the timer does not exist."""
        timer = self.get_timer_id(timer)
        return [c_report.timers[timer] for c_report in self.__client_reports]

    def get_timer_average_and_standard_deviation(self, timer: Union[int, str]) -> (float, float):
        """
        Returns a tuple with the average and the standard deviation of a (named) timer.
        """
        return average_and_standard_deviation(self.get_timer_times(timer))

    def get_timer_min_median_max(self, timer: Union[int, str]) -> (float, float, float):
        """
        Returns a tuple with the minimal, median and maximal reported times of a (named) timer.
        """
        return min_median_max(self.get_timer_times(timer))

    def get_timer_average(self, timer: Union[int, str]) -> float:
        """Returns the average of a (named) timer."""
        return self.get_timer_average_and_standard_deviation(timer)[0]

    def get_timer_standard_deviation(self, timer: Union[int, str]) -> float:
        """Returns the standard deviation of the reported times on a (named) timer."""
        return self.get_timer_average_and_standard_deviation(timer)[1]

    def get_timer_min(self, timer: Union[int, str]) -> float:
        """Returns the minimal reported time of a (named) timer."""
        return min(self.get_timer_times(timer))

    def get_timer_median(self, timer: Union[int, str]) -> float:
        """Returns the median reported time of a (named) timer."""
        return self.get_timer_min_median_max(timer)[1]

    def get_timer_max(self, timer: Union[int, str]) -> float:
        """Returns the maximal reported time of a (named) timer."""
        return max(self.get_timer_times(timer))

    def get_timer_data_sent(self, timer: Union[int, str]) -> List[str]:
        """Returns the data sent for the given (named) timer for each client."""
        timer = self.get_timer_id(timer)
        return [report.timers_data_sent[timer] for report in self.__client_reports]

    def get_timer_cpu_times(self, timer: Union[int, str]) -> List[float]:
        """Returns the CPU time (in seconds) for the given (named) timer for each client.

        The per-timer CPU time is reported by MP-SPDZ >= 0.4.3. For reports produced by
        older versions the per-timer CPU times are not available (``None``)."""
        timer = self.get_timer_id(timer)
        return [report.timers_cpu_time[timer] if report.timers_cpu_time is not None else None
                for report in self.__client_reports]

    def get_timer_cpu_time_average_and_standard_deviation(self, timer: Union[int, str]) -> (float, float):
        """Returns the average and standard deviation of a (named) timer's CPU time."""
        return average_and_standard_deviation(self.get_timer_cpu_times(timer))

    def get_timer_cpu_time_min_median_max(self, timer: Union[int, str]) -> (float, float, float):
        """Returns the minimal, median and maximal CPU time of a (named) timer."""
        return min_median_max(self.get_timer_cpu_times(timer))

    def get_timer_global_data_sent(self, timer: Union[int, str]) -> int:
        """Returns the global data sent for the given (named) timer in bytes."""
        tmp_timer_global_data = 0
        for tds in self.get_timer_data_sent(timer):
            if tds.endswith(" MB"):
                # We need to use sympy.Rational to avoid floating point errors in some (rare) cases
                import sympy
                tmp_timer_global_data += sympy.Rational(tds.rsplit(" ")[0]) * 10 ** 6
            else:
                logger.critical("Data not given in MB, aborting")
                raise NeonException("Data not given in MB")
        if tmp_timer_global_data.is_integer:
            tmp_timer_global_data = int(tmp_timer_global_data)
        else:
            logger.critical(f"Timer global data (in bytes) is not an integer")
            raise NeonException("Timer global data (in bytes) is not an integer")
        return tmp_timer_global_data

    def get_total_runtimes(self) -> List[float]:
        return [client_report.total_runtime for client_report in self.__client_reports]

    def get_average_total_runtime(self) -> float:
        return average_and_standard_deviation(self.get_total_runtimes())[0]

    def get_cpu_times(self) -> List[float]:
        return [client_report.cpu_time for client_report in self.__client_reports]

    def get_average_cpu_time(self) -> float:
        return average_and_standard_deviation(self.get_cpu_times())[0]

    def get_global_data_sent(self) -> int:
        """Returns global data sent in bytes"""
        # First check if they are all the same
        tmp_all_global_data = [client_report.global_data_sent for client_report in self.__client_reports]
        if not all([glob_data == tmp_all_global_data[0] for glob_data in tmp_all_global_data[1:]]):
            raise Exception(f"Global data not all the same: {tmp_all_global_data}")

        # Get bytes
        if tmp_all_global_data[0].endswith(" MB"):
            # We need to use sympy.Rational to avoid floating point errors in some (rare) cases
            tmp_global_data = sympy.Rational(tmp_all_global_data[0].rsplit(" ")[0]) * 10 ** 6
        else:
            logger.critical("Data not given in MB, aborting")
            raise NeonException("Data not given in MB")
        if tmp_global_data.is_integer:
            tmp_global_data = int(tmp_global_data)
        else:
            logger.critical(f"Global data (in bytes) is not an integer")
            raise NeonException("Global data (in bytes) is not an integer")
        return tmp_global_data

    def run_was_successfull(self) -> bool:
        # If there is any client computation report without a total runtime, then the execution failed
        if None in self.get_total_runtimes():
            return False
        else:
            return True

    # endregion

    def reduce_verbosity(self) -> "ComputationReport":
        return ComputationReport(
            program=self.program,
            pre_substitution_hash=self.pre_substitution_hash,
            substitutions=self.substitutions,
            program_hash=self.program_hash,
            timer_names=self.timer_names,
            protocol=self.protocol,
            operation_mode=self.operation_mode,
            delay=self.delay,
            outgoing_bandwidth=self.outgoing_bandwidth,
            incoming_bandwidth=self.incoming_bandwidth,
            bits_from_squares=self.bits_from_squares,
            batch_size=self.batch_size,
            bucket_size=self.bucket_size,
            mpspdz_version=self.mpspdz_version,
            client_reports=[report.reduce_verbosity() for report in self.client_reports],
            secret_before_computation=self.secrets_before_computation,
            secrets_after_computation=self.secrets_after_computation,
            custom_metadata=self.custom_metadata,
            max_cpu_usage=self.max_cpu_usage,
            max_memory_usage=self.max_memory_usage
        )

    def with_custom_metadata(self, custom_metadata: Any, overwrite: bool = False) -> "ComputationReport":
        if self.custom_metadata is not None and not overwrite:
            raise ValueError("Custom metadata is already set.")

        return ComputationReport(
            program=self.program,
            pre_substitution_hash=self.pre_substitution_hash,
            substitutions=self.substitutions,
            program_hash=self.program_hash,
            timer_names=self.timer_names,
            protocol=self.protocol,
            operation_mode=self.operation_mode,
            delay=self.delay,
            outgoing_bandwidth=self.outgoing_bandwidth,
            incoming_bandwidth=self.incoming_bandwidth,
            bits_from_squares=self.bits_from_squares,
            batch_size=self.batch_size,
            bucket_size=self.bucket_size,
            mpspdz_version=self.mpspdz_version,
            client_reports=self.client_reports,
            secret_before_computation=self.secrets_before_computation,
            secrets_after_computation=self.secrets_after_computation,
            custom_metadata=custom_metadata,
            max_cpu_usage=self.max_cpu_usage,
            max_memory_usage=self.max_memory_usage,
            experiment_spec=self.experiment_spec,
            custom_version=self.custom_version
        )

    def to_serializable_dict(self) -> Dict[str, any]:
        job_name = os.getenv("JOB_NAME")
        project_ID = os.getenv("PROJECT_ID")
        return {
            'job_name': job_name,
            'project_ID': project_ID,
            'parameters': {
                'program': self.__program,
                'pre_substitution_hash': self.__pre_substitution_hash,
                'substitutions': self.__substitutions,
                'timer_names': self.__timer_names,
                'program_hash': self.__program_hash,
                'protocol': self.__protocol,
                'operation_mode': self.__operation_mode.name,
                'delay': self.__delay,
                'outgoing_bandwidth': self.__outgoing_bandwidth,
                'incoming_bandwidth': self.__incoming_bandwidth,
                'bits_from_squares': self.__bits_from_squares,
                'batch_size': self.__batch_size,
                'bucket_size': self.__batch_size,
                'mpspdz_version': self.__mpspdz_version
            },
            'client_reports': [report.to_serializable_dict() for report in self.__client_reports],
            'secrets_before_computation': self.__secrets_before_computation,
            'secrets_after_computation': self.__secrets_after_computation,
            'custom_metadata': self.__custom_metadata,
            'max_cpu_usage': self.__max_cpu_usage,
            'max_memory_usage': self.__max_memory_usage,
            'custom_version': self.__custom_version
        }
    
    def send_to_server(self, url: str) -> None:
        data = self.to_serializable_dict()
        response = requests.post(url, json=data)
        if response.status_code == 200:
            print("Report successfully sent to the server.")
        else:
            print(f"Failed to send report: {response.status_code} {response.text}")

    def save_to_file(self, filename: str) -> None:
        with open(filename, 'w') as f:
            json.dump(self.to_serializable_dict(), f, indent=4)

    @staticmethod
    def from_serializable_dict(dict: Dict[str, any]) -> 'ComputationReport':
        parameters = dict['parameters']
        program = parameters['program']

        pre_substitution_hash = None
        if 'pre_substitution_hash' in parameters.keys():
            pre_substitution_hash = parameters['pre_substitution_hash']

        substitutions = parameters['substitutions']
        program_hash = parameters['program_hash']

        timer_names = None
        if 'timer_names' in parameters.keys():
            timer_names = {int(timer): name for (timer, name) in parameters['timer_names'].items()}

        protocol = parameters['protocol']
        operation_mode = OperationMode.from_name(parameters['operation_mode'])
        delay = parameters['delay']
        outgoing_bandwidth = parameters['outgoing_bandwidth']
        incoming_bandwidth = parameters['incoming_bandwidth']
        bits_from_squares = parameters['bits_from_squares']
        batch_size = parameters['batch_size']
        bucket_size = parameters['bucket_size']
        mpspdz_version = parameters['mpspdz_version']
        client_reports = [ClientComputationReport.from_serializable_dict(report) for report in dict['client_reports']]
        secrets_before_computation = dict.setdefault('secrets_before_computation', None)
        secrets_after_computation = dict.setdefault('secrets_after_computation', None)
        custom_metadata = dict.setdefault('custom_metadata', None)
        max_cpu_usage = dict.setdefault('max_cpu_usage', None)
        max_memory_usage = dict.setdefault('max_memory_usage', None)
        experiment_spec = dict.setdefault('experiment_spec', None)
        custom_version = dict.setdefault('custom_version', None)
        return ComputationReport(program,
                                 pre_substitution_hash,
                                 substitutions,
                                 program_hash,
                                 timer_names,
                                 protocol,
                                 operation_mode,
                                 delay,
                                 outgoing_bandwidth,
                                 incoming_bandwidth,
                                 bits_from_squares,
                                 batch_size,
                                 bucket_size,
                                 mpspdz_version,
                                 client_reports,
                                 secrets_before_computation,
                                 secrets_after_computation,
                                 custom_metadata,
                                 max_cpu_usage,
                                 max_memory_usage,
                                 experiment_spec=experiment_spec,
                                 custom_version=custom_version)

    @staticmethod
    def from_file(filename: str) -> 'ComputationReport':
        with open(filename, 'r') as f:
            report = ComputationReport.from_serializable_dict(json.load(f))
            # Record the filename since this report was loaded from a file
            report.__filename = filename
            return report

    # region Properties
    @property
    def program(self) -> str:
        return self.__program

    @property
    def filename(self) -> Optional[str]:
        return self.__filename

    @property
    def pre_substitution_hash(self) -> str:
        return self.__pre_substitution_hash

    @property
    def substitutions(self) -> Dict[str, str]:
        return self.__substitutions

    @property
    def program_hash(self) -> str:
        return self.__program_hash

    @property
    def timer_names(self) -> Dict[int, str]:
        return self.__timer_names

    @property
    def protocol(self) -> str:
        return self.__protocol

    @property
    def operation_mode(self) -> OperationMode:
        return self.__operation_mode

    @property
    def delay(self) -> Optional[str]:
        return self.__delay

    @property
    def outgoing_bandwidth(self) -> Optional[str]:
        return self.__outgoing_bandwidth

    @property
    def incoming_bandwidth(self) -> Optional[str]:
        return self.__incoming_bandwidth

    @property
    def network(self) -> Network:
        return Network(self.delay, self.incoming_bandwidth, self.outgoing_bandwidth)

    @property
    def bits_from_squares(self) -> bool:
        return self.__bits_from_squares

    @property
    def batch_size(self) -> int:
        return self.__batch_size

    @property
    def bucket_size(self) -> int:
        return self.__bucket_size

    @property
    def mpspdz_version(self) -> str:
        return self.__mpspdz_version

    @property
    def client_reports(self) -> List["ClientComputationReport"]:
        return self.__client_reports

    @property
    def secrets_before_computation(self) -> Optional[List[int]]:
        return self.__secrets_before_computation

    @property
    def secrets_after_computation(self) -> Optional[List[int]]:
        return self.__secrets_after_computation

    @property
    def number_of_parties(self) -> int:
        return len(self.__client_reports)

    @property
    def custom_metadata(self) -> Any:
        return self.__custom_metadata

    @property
    def max_cpu_usage(self) -> Optional[float]:
        return self.__max_cpu_usage
    
    @property
    def max_memory_usage(self) -> Optional[int]:
        return self.__max_memory_usage

    @max_cpu_usage.setter
    def max_cpu_usage(self, value: float) -> None:
        self.__max_cpu_usage = value
    
    @max_memory_usage.setter
    def max_memory_usage(self, value: int) -> None:
        self.__max_memory_usage = value

    @property
    def experiment_spec(self) -> dict[str, Any] | None:
        return self.__experiment_spec

    @property
    def custom_version(self) -> str | None:
        return self.__custom_version
# endregion


class ClientComputationReport:
    """The ClientComputation is a read only report on the client's view on one single MPC computation.
    It contains client-specific parameters and computation results, like timers or private outputs."""

    __ip: Optional[str]
    __input: Optional[str]

    __stdout: Optional[bytes]
    __stderr: Optional[bytes]

    __cpu_time: float
    __total_runtime: float
    # Wall-clock time MP-SPDZ spent on setup before the computation, in seconds.
    __setup_time: float | None = None
    __data_sent: Optional[str] = None
    __communication_rounds: Optional[int] = None
    __global_data_sent: Optional[str] = None
    # Per communication-pattern breakdown ("Communication details:" block), each entry
    # being {"channel": str, "data": "<x> MB", "rounds": int, "time": float | None}.
    __communication_details: list[dict] | None = None

    __offline_phase_time: float | None = None
    __offline_phase_rounds: int | None = None
    __offline_phase_data: str | None = None
    __offline_phase_cpu_time: float | None = None
    __online_phase_time: float | None = None
    __online_phase_rounds: int | None = None
    __online_phase_data: str | None = None
    __online_phase_cpu_time: float | None = None

    __timers: Dict[int, float]
    __timers_data_sent: Optional[Dict[int, str]] = None
    __timers_communication_rounds: dict[int, int] | None = None
    # CPU time (in seconds) consumed per timer, as reported by MP-SPDZ >= 0.4.3.
    __timers_cpu_time: dict[int, float] | None = None
    # Each lap tuple is
    #   (start_time, stop_time, start_rounds, stop_rounds, start_data, stop_data,
    #    idle_time, start_cpu_time, stop_cpu_time)
    # The two trailing CPU-time fields were added for MP-SPDZ >= 0.4.3; lap data
    # loaded from older reports keeps the legacy 7-tuple shape.
    __timers_lap_data: dict[int, list[tuple[float, float, int, int, str, str, float, float, float]]] | None = None
    # Internal storage: keep either a decoded dict in __timers_lap_data_decoded or
    # a base64 string in __timers_lap_data_compressed (to avoid unnecessary recompression).
    __timers_lap_data_decoded: dict[int, list[tuple[float, float, int, int, str, str, float, float, float]]] | None = None
    __timers_lap_data_compressed: str | None = None

    __preprocessing_cost: dict[str, dict[str, int]] | None = None

    __private_outputs: Optional[List[int]]

    def __init__(self,
                 ip: Optional[str],
                 input: Optional[str],
                 stdout: Optional[bytes],
                 stderr: Optional[bytes],
                 cpu_time: float,
                 total_runtime: float,
                 data_sent: Optional[str],
                 communication_rounds: Optional[int],
                 global_data_sent: Optional[str],
                 offline_phase_time: float | None = None,
                 offline_phase_rounds: int | None = None,
                 offline_phase_data: str | None = None,
                 online_phase_time: float | None = None,
                 online_phase_rounds: int | None = None,
                 online_phase_data: str | None = None,
                 timers: Dict[int, float] = None,
                 timers_data_sent: Dict[int, str] | None = None,
                 timers_communication_rounds: dict[int, int] | None = None,
                 timers_lap_data: dict[int, list[tuple[float, float, int, int, str, str, float, float, float]]] | None = None,
                 preprocessing_cost: dict[str, dict[str, int]] | None = None,
                 private_output: Optional[List[int]] = None,
                 timers_cpu_time: dict[int, float] | None = None,
                 offline_phase_cpu_time: float | None = None,
                 online_phase_cpu_time: float | None = None,
                 setup_time: float | None = None,
                 communication_details: list[dict] | None = None):
        self.__ip = ip
        self.__input = input
        self.__stdout = stdout
        self.__stderr = stderr
        self.__cpu_time = cpu_time
        self.__total_runtime = total_runtime
        self.__setup_time = setup_time
        self.__data_sent = data_sent
        self.__communication_rounds = communication_rounds
        self.__global_data_sent = global_data_sent
        self.__communication_details = communication_details
        self.__offline_phase_time = offline_phase_time
        self.__offline_phase_rounds = offline_phase_rounds
        self.__offline_phase_data = offline_phase_data
        self.__offline_phase_cpu_time = offline_phase_cpu_time
        self.__online_phase_time = online_phase_time
        self.__online_phase_rounds = online_phase_rounds
        self.__online_phase_data = online_phase_data
        self.__online_phase_cpu_time = online_phase_cpu_time
        self.__timers = timers
        self.__timers_data_sent = timers_data_sent
        self.__timers_communication_rounds = timers_communication_rounds
        self.__timers_cpu_time = timers_cpu_time
        # Initialize lap data storage. If callers pass decoded lap data, keep it decoded
        # and leave compressed form empty until serialization. If created from serialized
        # input, `from_serializable_dict` will populate `__timers_lap_data_compressed` instead.
        self.__timers_lap_data_decoded = timers_lap_data
        self.__timers_lap_data_compressed = None
        self.__preprocessing_cost = preprocessing_cost
        self.__private_outputs = private_output

    def reduce_verbosity(self) -> "ClientComputationReport":
        return ClientComputationReport(
            ip=None,
            input=self.input,
            stdout=None,
            stderr=None,
            cpu_time=self.cpu_time,
            total_runtime=self.total_runtime,
            setup_time=self.setup_time,
            data_sent=self.data_sent,
            global_data_sent=self.global_data_sent,
            communication_rounds=self.communication_rounds,
            communication_details=self.communication_details,
            timers=self.timers,
            timers_data_sent=self.timers_data_sent,
            timers_communication_rounds=self.timers_communication_rounds,
            timers_cpu_time=self.timers_cpu_time,
            timers_lap_data=self.timers_lap_data,
            preprocessing_cost=self.preprocessing_cost,
            private_output=self.private_outputs,
            offline_phase_time=self.offline_phase_time,
            offline_phase_rounds=self.offline_phase_rounds,
            offline_phase_data=self.offline_phase_data,
            offline_phase_cpu_time=self.offline_phase_cpu_time,
            online_phase_time=self.online_phase_time,
            online_phase_rounds=self.online_phase_rounds,
            online_phase_data=self.online_phase_data,
            online_phase_cpu_time=self.online_phase_cpu_time
        )

    def to_serializable_dict(self) -> Dict[str, any]:
        return {
            'ip': self.__ip,
            'input': self.__input,
            'stdout': b64encode(self.__stdout).decode('utf-8') if self.__stdout else None,
            'stderr': b64encode(self.__stderr).decode('utf-8') if self.__stderr else None,
            'cpu_time': self.__cpu_time,
            'total_runtime': self.__total_runtime,
            'setup_time': self.__setup_time,
            'data_sent': self.__data_sent,
            'communication_rounds': self.__communication_rounds,
            'global_data_sent': self.__global_data_sent,
            'communication_details': self.__communication_details,
            'offline_phase': {
                "time": self.__offline_phase_time,
                "rounds": self.__offline_phase_rounds,
                "data": self.__offline_phase_data,
                "cpu_time": self.__offline_phase_cpu_time
            },
            "online_phase": {
                "time": self.__online_phase_time,
                "rounds": self.__online_phase_rounds,
                "data": self.__online_phase_data,
                "cpu_time": self.__online_phase_cpu_time
            },
                'timers': self.__timers,
                'timers_data_sent': self.__timers_data_sent,
                'timers_communication_rounds': self.__timers_communication_rounds,
                'timers_cpu_time': self.__timers_cpu_time,
                # Prefer to reuse the original compressed representation if available to
                # avoid decompressing and re-compressing. If not present but we have a
                # decoded representation, compress it now and cache the result.
                'compressed_timers_lap_data': (
                    self.__timers_lap_data_compressed
                    if self.__timers_lap_data_compressed is not None
                    else (ClientComputationReport.compress_timer_lap_data(self.__timers_lap_data_decoded)
                          if self.__timers_lap_data_decoded is not None else None)
                ),
            'preprocessing_cost': self.__preprocessing_cost,
            'private_outputs': self.__private_outputs
        }

    @staticmethod
    def from_serializable_dict(dict: Dict[str, any]) -> 'ClientComputationReport':
        ip = dict['ip'] if 'ip' in dict else None
        input = dict['input'] if 'input' in dict else None
        stdout = b64decode(dict['stdout'].encode('utf-8')) if ('stdout' in dict and dict['stdout']) else None
        stderr = b64decode(dict['stderr'].encode('utf-8')) if ('stderr' in dict and dict['stderr']) else None
        cpu_time = dict['cpu_time']
        total_runtime = dict['total_runtime']
        setup_time = dict['setup_time'] if 'setup_time' in dict.keys() else None
        data_sent = dict['data_sent'] if 'data_sent' in dict.keys() else None
        communication_rounds = dict['communication_rounds'] if 'communication_rounds' in dict.keys() else None
        global_data_sent = dict['global_data_sent'] if 'global_data_sent' in dict.keys() else None
        communication_details = dict['communication_details'] if 'communication_details' in dict.keys() else None
        timers = {int(key): value for (key, value) in dict['timers'].items()}
        timers_data_sent = None
        if 'timers_data_sent' in dict and dict['timers_data_sent'] is not None:
            timers_data_sent = {int(key): value for (key, value) in dict['timers_data_sent'].items()}
        timers_communication_rounds = None
        if 'timers_communication_rounds' in dict and dict['timers_communication_rounds'] is not None:
            timers_communication_rounds = {int(key): value for (key, value) in
                                           dict['timers_communication_rounds'].items()}
        timers_cpu_time = None
        if 'timers_cpu_time' in dict and dict['timers_cpu_time'] is not None:
            timers_cpu_time = {int(key): value for (key, value) in dict['timers_cpu_time'].items()}
        private_outputs = dict['private_outputs'] if 'private_outputs' in dict else None

        preprocessing_cost = dict['preprocessing_cost'] if 'preprocessing_cost' in dict.keys() else None

        lap_data = None
        compressed_lap_data = None
        # Support legacy raw 'timers_lap_data' field (decoded), or new 'compressed_timers_lap_data'.
        if 'timers_lap_data' in dict and dict['timers_lap_data'] is not None:
            lap_data = {int(timer): [tuple(x) for x in laps] for (timer, laps) in dict['timers_lap_data'].items()}
        if 'compressed_timers_lap_data' in dict and dict['compressed_timers_lap_data'] is not None:
            compressed_lap_data = dict['compressed_timers_lap_data']

        # offline/online phases
        if 'offline_phase' in dict and dict['offline_phase'] is not None:
            offline = dict['offline_phase']
            offline_time = offline.get('time')
            offline_rounds = offline.get('rounds')
            offline_data = offline.get('data')
            offline_cpu_time = offline.get('cpu_time')
        else:
            offline_time = None
            offline_rounds = None
            offline_data = None
            offline_cpu_time = None

        if 'online_phase' in dict and dict['online_phase'] is not None:
            online = dict['online_phase']
            online_time = online.get('time')
            online_rounds = online.get('rounds')
            online_data = online.get('data')
            online_cpu_time = online.get('cpu_time')
        else:
            online_time = None
            online_rounds = None
            online_data = None
            online_cpu_time = None

        report = ClientComputationReport(ip,
                           input,
                           stdout,
                           stderr,
                           cpu_time,
                           total_runtime,
                           data_sent,
                           communication_rounds,
                           global_data_sent,
                           offline_time,
                           offline_rounds,
                           offline_data,
                           online_time,
                           online_rounds,
                           online_data,
                           timers,
                           timers_data_sent,
                           timers_communication_rounds,
                           lap_data,
                           preprocessing_cost,
                           private_outputs,
                           timers_cpu_time=timers_cpu_time,
                           offline_phase_cpu_time=offline_cpu_time,
                           online_phase_cpu_time=online_cpu_time,
                           setup_time=setup_time,
                           communication_details=communication_details)
        # If we received a compressed representation, store it for lazy decompression
        # and avoid recompressing when serializing again.
        if compressed_lap_data is not None:
            report.__timers_lap_data_compressed = compressed_lap_data
            report.__timers_lap_data_decoded = None

        return report
    
    @staticmethod
    def compress_timer_lap_data(data: dict[int, list[tuple[float, float, int, int, str, str, float]]]) -> str:
        def to_bytes(data_string: str) -> int:
            value = None
            unit = None
            try:
                import string
                value = data_string.rstrip(string.ascii_letters + " ")
                assert data_string.startswith(value)
                unit = data_string[len(value):].lstrip(" ")
                assert unit == "MB"
                return int(float(value) * 1_000_000)
            except AssertionError as e:
                print(data_string, value, unit)
                raise e

        # Each stored entry is (stop_time, rounds, data_bytes, idle_time[, stop_cpu_time]).
        # The trailing CPU time (absolute, like the time) is only present for lap data
        # captured with MP-SPDZ >= 0.4.3; legacy 7-tuple laps omit it.
        partial_deltas: dict[int, list[tuple]] = {}
        for (timer_id, laps) in data.items():
            has_cpu = len(laps[0]) > 8
            last_time = laps[0][1]
            last_rounds = laps[0][3]
            last_data_bytes = to_bytes(laps[0][5])
            last_cpu = laps[0][8] if has_cpu else None
            first_entry = [last_time, last_rounds, last_data_bytes, laps[0][6]]
            if has_cpu:
                first_entry.append(last_cpu)
            timer_partial_deltas = [tuple(first_entry)]

            for lap in laps[1:]:
                assert lap[0] == last_time
                assert lap[2] == last_rounds
                assert to_bytes(lap[4]) == last_data_bytes
                entry = [lap[1], lap[3] - last_rounds, to_bytes(lap[5]) - last_data_bytes, lap[6]]
                if has_cpu:
                    assert lap[7] == last_cpu
                    entry.append(lap[8])
                timer_partial_deltas.append(tuple(entry))

                last_time = lap[1]
                last_rounds = lap[3]
                last_data_bytes = to_bytes(lap[5])
                if has_cpu:
                    last_cpu = lap[8]
            partial_deltas[timer_id] = timer_partial_deltas
        
        encoded_partial_detlas = json.dumps(partial_deltas).encode('utf-8')
        compressed_deltas = zlib.compress(encoded_partial_detlas)
        jsonsafe = base64.b64encode(compressed_deltas)
        # Return a text-safe base64 string for JSON serialization
        return jsonsafe.decode('ascii')

    @staticmethod
    def decompress_timer_lap_data(comp: str | bytes) -> dict[int, list[tuple[float, float, int, int, str, str, float]]]:
        """Reverse of `compress_timer_lap_data`.

        Accepts a base64-encoded, zlib-compressed JSON blob (bytes or str) and
        returns the reconstructed `timers_lap_data` structure.
        """
        if comp is None:
            return None

        # Accept either bytes (result of b64encode) or str
        if isinstance(comp, str):
            comp_bytes = comp.encode('utf-8')
        else:
            comp_bytes = comp

        try:
            compressed = base64.b64decode(comp_bytes)
            decoded = zlib.decompress(compressed)
            partial = json.loads(decoded.decode('utf-8'))
        except Exception as e:
            raise NeonException("Failed to decode compressed timers lap data") from e

        def bytes_to_mb_str(b: int) -> str:
            mb = b / 1_000_000
            s = format(mb, 'f').rstrip('0').rstrip('.')
            if s == '':
                s = '0'
            return f"{s} MB"

        result: dict[int, list[tuple[float, float, int, int, str, str, float]]] = {}
        for (timer_key, entries) in partial.items():
            timer_id = int(timer_key)
            laps: list[tuple[float, float, int, int, str, str, float]] = []

            prev_time = 0
            prev_rounds = 0
            prev_bytes = 0
            prev_cpu = 0

            for i, e in enumerate(entries):
                # each entry is [time, rounds_or_abs, bytes_or_abs, idle_time[, stop_cpu_time]].
                # The trailing CPU time is only present for lap data captured with
                # MP-SPDZ >= 0.4.3.
                end_time = float(e[0])
                rounds_val = int(e[1])
                bytes_val = int(e[2])
                float_val = float(e[3])
                has_cpu = len(e) > 4
                end_cpu = float(e[4]) if has_cpu else None

                if i == 0:
                    # first entry contains absolute values; create a zero-length initial lap
                    prev_time = end_time
                    prev_rounds = rounds_val
                    prev_bytes = bytes_val
                    prev_cpu = end_cpu
                    start_time = prev_time
                    start_rounds = prev_rounds
                    start_bytes = prev_bytes
                    start_cpu = prev_cpu
                    end_rounds = prev_rounds
                    end_bytes = prev_bytes
                else:
                    start_time = prev_time
                    start_rounds = prev_rounds
                    start_bytes = prev_bytes
                    start_cpu = prev_cpu
                    end_rounds = prev_rounds + rounds_val
                    end_bytes = prev_bytes + bytes_val

                start_data = bytes_to_mb_str(start_bytes)
                end_data = bytes_to_mb_str(end_bytes)

                if has_cpu:
                    laps.append((start_time, end_time, start_rounds, end_rounds, start_data, end_data,
                                 float_val, start_cpu, end_cpu))
                else:
                    laps.append((start_time, end_time, start_rounds, end_rounds, start_data, end_data, float_val))

                prev_time = end_time
                prev_rounds = end_rounds
                prev_bytes = end_bytes
                prev_cpu = end_cpu

            result[timer_id] = laps

        return result

    @property
    def ip(self) -> Optional[str]:
        return self.__ip

    @property
    def input(self) -> Optional[str]:
        return self.__input

    @property
    def stdout(self) -> Optional[bytes]:
        return self.__stdout

    @property
    def stderr(self) -> Optional[bytes]:
        return self.__stderr

    @property
    def cpu_time(self) -> float:
        return self.__cpu_time

    @property
    def total_runtime(self) -> float:
        return self.__total_runtime

    @property
    def setup_time(self) -> Optional[float]:
        """Wall-clock time (in seconds) MP-SPDZ spent on setup before the computation."""
        return self.__setup_time

    @property
    def data_sent(self) -> str:
        return self.__data_sent

    @property
    def communication_rounds(self) -> Optional[int]:
        return self.__communication_rounds

    @property
    def global_data_sent(self) -> str:
        return self.__global_data_sent

    @property
    def communication_details(self) -> list[dict] | None:
        """The per communication-pattern breakdown from MP-SPDZ's "Communication details:"
        block. Each entry is {"channel": str, "data": "<x> MB", "rounds": int,
        "time": float | None}."""
        return self.__communication_details
    
    @property
    def offline_phase_time(self) -> Optional[float]:
        return self.__offline_phase_time

    @property
    def offline_phase_rounds(self) -> Optional[int]:
        return self.__offline_phase_rounds

    @property
    def offline_phase_data(self) -> Optional[str]:
        return self.__offline_phase_data

    @property
    def offline_phase_cpu_time(self) -> Optional[float]:
        return self.__offline_phase_cpu_time

    @property
    def online_phase_time(self) -> Optional[float]:
        return self.__online_phase_time

    @property
    def online_phase_rounds(self) -> Optional[int]:
        return self.__online_phase_rounds

    @property
    def online_phase_data(self) -> Optional[str]:
        return self.__online_phase_data

    @property
    def online_phase_cpu_time(self) -> Optional[float]:
        return self.__online_phase_cpu_time

    @property
    def timers(self) -> Dict[int, float]:
        return self.__timers

    @property
    def timers_data_sent(self) -> Optional[Dict[int, str]]:
        return self.__timers_data_sent

    @property
    def timers_communication_rounds(self) -> dict[int, str] | None:
        return self.__timers_communication_rounds

    @property
    def timers_cpu_time(self) -> dict[int, float] | None:
        """CPU time (in seconds) consumed per timer, as reported by MP-SPDZ >= 0.4.3."""
        return self.__timers_cpu_time
    
    @property
    def timers_lap_data(self) -> dict[int, list[tuple[float, float, int, int, str, str, float]]] | None:
        # Lazy decompress: if we only have a compressed representation, decompress on first access.
        if self.__timers_lap_data_decoded is not None:
            return self.__timers_lap_data_decoded
        if self.__timers_lap_data_compressed is None:
            return None
        # Decompress and cache decoded form, but keep compressed form to allow re-serialization
        decoded = ClientComputationReport.decompress_timer_lap_data(self.__timers_lap_data_compressed)
        self.__timers_lap_data_decoded = decoded
        return decoded

    @property
    def preprocessing_cost(self) -> dict[str, dict[str, int]] | None:
        return self.__preprocessing_cost

    @property
    def private_outputs(self) -> Optional[List[int]]:
        return self.__private_outputs


class ComputationReportList(UserList):
    """
    A collection of multiple computation reports. Facilitates combining the information of these reports.
    """

    def __init__(self, reports: Iterable[ComputationReport] = None):
        if not reports:
            reports = []
        super().__init__(reports)

    def get_all_timer_times(self, timer: Union[int, str]) -> List[float]:
        """
        Collects the raw timer times from all computation reports. If a string is given, the function will look
        up the timer belonging to that timer name.
        """
        result = []
        for report in self:
            result.extend(report.get_timer_times(timer))
        return result

    def get_timer_average_and_standard_deviation(self, timer: Union[int, str]) -> (float, float):
        """Returns the average and standard deviation of all reported times on a (named) timer."""
        return average_and_standard_deviation(self.get_all_timer_times(timer))

    def get_timer_min_median_max(self, timer: Union[int, str]) -> (float, float, float):
        """Returns the minimal, median and maximal reported time on a (named) timer."""
        return min_median_max(self.get_all_timer_times(timer))

    def get_timer_average(self, timer: Union[int, str]) -> float:
        """Returns the average of a (named) timer."""
        return self.get_timer_average_and_standard_deviation(timer)[0]

    def get_timer_standard_deviation(self, timer: Union[int, str]) -> float:
        """Returns the standard deviation of the reported times on a (named) timer."""
        return self.get_timer_average_and_standard_deviation(timer)[1]

    def get_timer_min(self, timer: Union[int, str]) -> float:
        """Returns the minimal reported time of a (named) timer."""
        return min(self.get_all_timer_times(timer))

    def get_timer_median(self, timer: Union[int, str]) -> float:
        """Returns the median reported time of a (named) timer."""
        return self.get_timer_min_median_max(timer)[1]

    def get_timer_max(self, timer: Union[int, str]) -> float:
        """Returns the maximal reported time of a (named) timer."""
        return max(self.get_all_timer_times(timer))

    def get_total_runtimes(self) -> List[float]:
        result = []
        for report in self:
            result.extend(report.get_total_runtimes())
        return result

    def get_total_runtime_average_and_standard_deviation(self) -> (float, float):
        return average_and_standard_deviation(self.get_total_runtimes())

    def get_total_runtime_min_median_max(self) -> (float, float, float):
        return min_median_max(self.get_total_runtimes())

    def filter(self, f: Callable[[ComputationReport], bool]) -> "ComputationReportList":
        """Returns a ComputationReportList containing a reports that satisfy the filter."""
        result = ComputationReportList()
        for report in self:
            if f(report):
                result.append(report)
        return result

    def group(self, grouper: Callable[[ComputationReport], Hashable]) -> Dict[Any, "ComputationReportList"]:
        """
        Groups the reports according to the passed function. The passed function must return a
        hashable type, e.g. an integer, a string, or a tuple, that will be used as group identifier.

        Example: reports.group(lambda report: report.number_of_parties) groups the reports by the number of parties.
        """
        result = {}
        for report in self:
            result.setdefault(grouper(report), ComputationReportList()).append(report)
        return result

    def group_and_sort_groups(self,
                              grouper: Callable[[ComputationReport], Hashable],
                              key: Callable[[Tuple[Any, "ComputationReportList"]], int] = lambda a: a[0],
                              reverse: bool = False) -> List[Tuple[Any, "ComputationReportList"]]:
        groups = [(key, reports) for (key, reports) in self.group(grouper).items()]
        groups.sort(key=key, reverse=reverse)
        return groups

    def group_by_number_of_parties(self) -> Dict[int, "ComputationReportList"]:
        return self.group(lambda report: report.number_of_parties)

    def group_by_number_of_parties_sorted(self) -> List[Tuple[Any, "ComputationReportList"]]:
        return self.group_and_sort_groups(lambda report: report.number_of_parties)

    def group_by_delay(self) -> Dict[Optional[str], "ComputationReportList"]:
        return self.group(lambda report: report.delay)

    def group_by_outgoing_bandwidth(self) -> Dict[Optional[str], "ComputationReportList"]:
        return self.group(lambda report: report.outgoing_bandwidth)

    def group_by_network(self) -> Dict[Network, "ComputationReportList"]:
        return self.group(lambda report: report.network)

    def save_to_file(self, filename: str) -> None:
        with open(filename, 'w') as f:
            json.dump([report.to_serializable_dict() for report in self], f, indent=4)

    @staticmethod
    def from_folder(foldername: str) -> "ComputationReportList":
        result = ComputationReportList()
        for file in os.listdir(foldername):
            if file.startswith("computation-") and os.path.isfile(join_path_abs(foldername, file)):
                result.append(ComputationReport.from_file(join_path_abs(foldername, file)))
            elif os.path.isdir(join_path_abs(foldername, file)):
                result.extend(ComputationReportList.from_folder(join_path_abs(foldername, file)))
        return result

    @staticmethod
    def from_logs(timestamp: str, workdir: str | None = None) -> "ComputationReportList":
        """Given the timestamp that identifies a log, that may or may not be compressed, it will load the computation reports from that log.
        If the log is compressed, it will be extracted first."""

        # Check if the corresponding log is extracted.
        log_root_folder = join_path_abs(get_workdir_or_default_workdir(workdir), "logs")
        timestamp_folder = join_path_abs(log_root_folder, timestamp)
        if os.path.isdir(timestamp_folder):
            return ComputationReportList.from_folder(timestamp_folder)

        # Check if the zip file for that log exists.
        zip_filename = join_path_abs(log_root_folder, timestamp + '.tar.zst')
        if not os.path.isfile(zip_filename):
            raise NeonException(f"Can't find old log with timestamp \"{timestamp}\".")

        # Prepare the extraction destination.
        extraction_folder = join_path_abs(log_root_folder, 'extracted')
        os.mkdir(extraction_folder)

        # Extract, load computation reports, delete extracted data.
        subprocess.run(['tar', '-I', 'zstd', '-xvf', zip_filename], check=True, cwd=extraction_folder)

        result = ComputationReportList.from_folder(extraction_folder)
        shutil.rmtree(extraction_folder)
        return result
