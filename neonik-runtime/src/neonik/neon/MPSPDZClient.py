import logging.config
import math
import os
import re
import threading
from typing import List, Dict, Optional

from tqdm import tqdm

from . import helper  # pylint: disable=E0402
from .XIO import LocalXIO, LocalVirtualXIO, DistributedXIO, XIO  # pylint: disable=E0402
from .computationreport import ClientComputationReport
from .operationmode import OperationMode
from .neonconfig import NeonConfig, ReportVerbosityLevel
from .protocol import Protocol, Domain

logging.config.fileConfig(
    helper.join_path_abs(os.path.dirname(__file__), '../config/logging.conf'),
    disable_existing_loggers=False)
# logger = logging.getLogger(__name__)
logger = logging.getLogger("Local Client")

# Matches a "<time> seconds" phase segment, optionally followed by the
# "(<data> MB, <rounds> rounds[, <cpu> CPU seconds])" communication breakdown.
# The trailing ", <cpu> CPU seconds" only exists since MP-SPDZ 0.4.3, and the
# whole parenthetical is omitted by MP-SPDZ when no data was sent in that phase.
_PHASE_RE = re.compile(
    r"(?P<time>\S+) seconds"
    r"(?: \((?P<data>\S+ MB), (?P<rounds>\d+) rounds(?:, (?P<cpu>\S+) CPU seconds)?\))?"
)

# Matches a single line of the "Communication details:" block, e.g.
#   Sending to all 1.6e-05 MB in 1 rounds, taking 6.7015e-05 seconds
# The "taking <time> seconds" suffix is present in both MP-SPDZ 0.4.2 and 0.4.3.
_COMM_DETAIL_RE = re.compile(
    r"^(?P<channel>.+?) (?P<data>\S+ MB) in (?P<rounds>\d+) rounds"
    r"(?:, taking (?P<time>\S+) seconds)?$"
)


def parse_mpspdz_output(stdout: bytes, stderr: bytes) -> dict:
    """Parse the stdout/stderr of a single MP-SPDZ party into report fields.

    Supports MP-SPDZ 0.4.2 and 0.4.3. Since 0.4.3, every "(<MB>, <rounds>)"
    communication breakdown additionally reports the consumed CPU time as a
    trailing ", <x> CPU seconds"; the fields carrying it (``timers_cpu_time``,
    ``online_phase_cpu_time``, ``offline_phase_cpu_time`` and the per-lap CPU
    times in ``timers_lap_data``) are ``None`` for 0.4.2 output.
    """
    timer_start_states: dict[int, tuple[float, str, int, float, float | None] | None] = {}
    current_preprocessing_type: str | None = None

    timers = {}
    timers_data_sent = {}
    timers_comm_rounds = {}
    timers_cpu_time = {}
    cpu_time = None
    total_runtime = None
    setup_time = None
    data_sent = None
    global_data_sent = None
    global_comm_rounds = None
    online_phase_time = None
    online_phase_data = None
    online_phase_rounds = None
    online_phase_cpu_time = None
    offline_phase_time = None
    offline_phase_data = None
    offline_phase_rounds = None
    offline_phase_cpu_time = None
    communication_details = []

    # Since MP-SPDZ 0.4.3, the (MB, rounds) breakdown accompanying every timer
    # additionally reports the consumed CPU time, e.g.
    #   Starting timer 1 at 0 (0 MB, 0 rounds, 0 CPU seconds) after 7.283e-06
    #   Stopped timer 1 at 0.00022 (4.8e-05 MB, 3 rounds, 0.00018 CPU seconds)
    # MP-SPDZ 0.4.2 omits the ", <x> CPU seconds" part:
    #   Starting timer 1 at 0 (0 MB, 0 rounds) after 6.803e-06
    #   Stopped timer 1 at 0.00016 (4.8e-05 MB, 3 rounds)
    # The rounds token stays at index 7 and the idle time is always the last
    # token, so both versions are parsed by keying off "CPU seconds".
    timers_lap_data: dict[int, list[tuple]] = {}
    preprocessing_cost: dict[str, dict[str, int]] = {}

    for line in stdout.decode("utf-8").splitlines():
        parts = line.split()

        if line.startswith("Starting timer"):
            timer = int(parts[2])
            start_time = float(parts[4])
            start_data = parts[5].lstrip("(") + " " + parts[6].rstrip(",")
            start_rounds = int(parts[7])
            start_cpu_time = float(parts[9]) if "CPU seconds" in line else None
            idle_time = float(parts[-1])

            assert timer_start_states.get(timer, None) == None
            timer_start_states[timer] = (start_time, start_data, start_rounds, idle_time, start_cpu_time)
        if line.startswith("Stopped timer"):
            timer = int(parts[2])
            stop_time = float(parts[4])
            stop_data = parts[5].lstrip("(") + " " + parts[6].rstrip(",")
            stop_rounds = int(parts[7])
            stop_cpu_time = float(parts[9]) if "CPU seconds" in line else None

            assert timer in timer_start_states
            (start_time, start_data, start_rounds, idle_time, start_cpu_time) = timer_start_states[timer]
            if stop_cpu_time is not None:
                # MP-SPDZ >= 0.4.3: keep the per-lap CPU time as two trailing fields.
                lap = (start_time, stop_time, start_rounds, stop_rounds, start_data, stop_data, idle_time,
                       start_cpu_time, stop_cpu_time)
            else:
                # MP-SPDZ 0.4.2: legacy 7-tuple without CPU time.
                lap = (start_time, stop_time, start_rounds, stop_rounds, start_data, stop_data, idle_time)
            timers_lap_data.setdefault(timer, []).append(lap)
            timer_start_states[timer] = None

    in_communication_details = False
    for line in stderr.decode("utf-8").splitlines():
        parts = line.split()

        # The "Communication details:" block (present in 0.4.2 and 0.4.3) lists the
        # data, rounds and time per communication pattern, e.g.
        #   Sending to all 1.6e-05 MB in 1 rounds, taking 6.7015e-05 seconds
        if line.startswith("Communication details"):
            in_communication_details = True
            continue
        if in_communication_details:
            match = _COMM_DETAIL_RE.match(line)
            if match is not None:
                communication_details.append({
                    "channel": match.group("channel"),
                    "data": match.group("data"),
                    "rounds": int(match.group("rounds")),
                    "time": float(match.group("time")) if match.group("time") is not None else None,
                })
                continue
            in_communication_details = False

        if line.startswith('CPU time ='):
            cpu_time = float(line.split()[3])
        elif line.startswith('Time ='):
            total_runtime = float(line.split()[2])
        elif line.startswith('Time'):
            # MP-SPDZ >= 0.4.3 appends ", <x> CPU seconds" to the per-timer line:
            #   Time1 = 0.000223799 seconds (4.8e-05 MB, 3 rounds, 0.00018568 CPU seconds)
            # MP-SPDZ 0.4.2 has no CPU time:
            #   Time1 = 0.000160496 seconds (4.8e-05 MB, 3 rounds)
            # The rounds token is at index 6 in both cases.
            timer_id = int(parts[0][4:])
            timers[timer_id] = float(parts[2])
            timers_data_sent[timer_id] = parts[4][1:] + ' ' + parts[5][:-1]
            timers_comm_rounds[timer_id] = int(parts[6])
            if "CPU seconds" in line:
                timers_cpu_time[timer_id] = float(parts[8])
        if line.startswith('Setup took'):
            setup_time = float(parts[2])
        if line.startswith('Data sent ='):
            data_sent = parts[3] + " " + parts[4]
            if "rounds" in line:
                global_comm_rounds = int(parts[6][1:])
        if line.startswith('Global data sent ='):
            global_data_sent = parts[4] + " " + parts[5]
        if line.startswith('Spent') and "online phase" in line:
            # The online/offline phase breakdown, e.g. (MP-SPDZ >= 0.4.3):
            #   Spent 0.00033 seconds (4.8e-05 MB, 3 rounds, 0.0003 CPU seconds) on the
            #   online phase and 0.0001 seconds on the preprocessing/offline phase.
            # MP-SPDZ 0.4.2 omits ", <x> CPU seconds"; either version omits the whole
            # parenthetical for a phase in which no data was sent. Parsing via regex
            # keeps this robust across versions and against missing breakdowns.
            online_segment, _, offline_segment = line.partition(" on the online phase and ")
            online_match = _PHASE_RE.search(online_segment)
            if online_match is not None:
                online_phase_time = float(online_match.group("time"))
                online_phase_data = online_match.group("data")
                online_phase_rounds = (int(online_match.group("rounds"))
                                       if online_match.group("rounds") is not None else None)
                online_phase_cpu_time = (float(online_match.group("cpu"))
                                         if online_match.group("cpu") is not None else None)
            offline_match = _PHASE_RE.search(offline_segment)
            if offline_match is not None:
                offline_phase_time = float(offline_match.group("time"))
                offline_phase_data = offline_match.group("data")
                offline_phase_rounds = (int(offline_match.group("rounds"))
                                        if offline_match.group("rounds") is not None else None)
                offline_phase_cpu_time = (float(offline_match.group("cpu"))
                                          if offline_match.group("cpu") is not None else None)
        # print(parts)
        if len(parts) > 0 and parts[0] == "Type":
            current_preprocessing_type = parts[1]
        if current_preprocessing_type is not None:
            try:
                preprocessing_cost.setdefault(current_preprocessing_type, {})[parts[1]] = int(parts[0])
            except ValueError:
                pass

    # The per-timer CPU time only exists since MP-SPDZ 0.4.3; keep it as None
    # (rather than an empty dict) for older versions.
    if not timers_cpu_time:
        timers_cpu_time = None

    return {
        "cpu_time": cpu_time,
        "total_runtime": total_runtime,
        "setup_time": setup_time,
        "data_sent": data_sent,
        "global_comm_rounds": global_comm_rounds,
        "global_data_sent": global_data_sent,
        "communication_details": communication_details,
        "online_phase_time": online_phase_time,
        "online_phase_rounds": online_phase_rounds,
        "online_phase_data": online_phase_data,
        "online_phase_cpu_time": online_phase_cpu_time,
        "offline_phase_time": offline_phase_time,
        "offline_phase_rounds": offline_phase_rounds,
        "offline_phase_data": offline_phase_data,
        "offline_phase_cpu_time": offline_phase_cpu_time,
        "timers": timers,
        "timers_data_sent": timers_data_sent,
        "timers_comm_rounds": timers_comm_rounds,
        "timers_cpu_time": timers_cpu_time,
        "timers_lap_data": timers_lap_data,
        "preprocessing_cost": preprocessing_cost,
    }


class Client:
    """
    The Client class is responsible for launching MP-SPDZ and capturing and parsing it's output.

    Parameters
    ----------

    operation_mode: OperationMode
        The operation mode of the current NEON instance.
    config: NeonConfig
        The config used in the current NEON instance.
    ip: str
        The IP address of the local client.

    Attributes
    ----------
    """

    operation_mode: OperationMode

    path_to_mpspdz: str
    path_to_mpspdz_copy: str
    input: str
    id: int
    ip: str
    config: NeonConfig
    starting_IP: str

    prime: Optional[int]
    R: int
    R_inv: int

    xio: XIO
    __stdout_read_thread: threading.Thread
    __stderr_read_thread: threading.Thread

    last_output: str
    stdout: bytes = None
    stderr: bytes = None
    timers: Dict[str, float] = None

    def __init__(self, operation_mode: OperationMode, config: NeonConfig, ip: str, party_id: int):
        self.operation_mode = operation_mode
        self.input = ""
        self.id = party_id
        self.config = config
        self.ip = ip

        self.path_to_mpspdz = config.local_mpspdz_path
        self.path_to_mpspdz_copy = config.compilation_target_path

        if operation_mode == OperationMode.LOCAL:
            self.xio = LocalXIO(config)
        elif operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.xio = LocalVirtualXIO(config)
        elif operation_mode == OperationMode.DISTRIBUTED:
            self.xio = DistributedXIO(config, ip)
        else:
            raise Exception(f"Unsupported mode of operation: {operation_mode.name}")

        self.prime = config.prime
        if self.prime:
            self.R = helper.get_R_for_prime(self.prime)
            self.R_inv = helper.mod_inverse(self.R, self.prime)
        self.starting_IP = "127.0.0.1"
        self.last_output = ""

    def start_computation(self, amount_of_parties: int, program: str, protocol: Protocol):
        """Performs preperations and launches MP-SPDZ and caputures it's output.

        Parameters
        ----------

        amount_of_parties: int
            Total amount of parties participating in the SMPC.
        program: str
            Program to be executed
        protocol: Protocol
            Protocol to be used for the computations.
        """
        if not program or program == "":
            logger.critical("No program specified for execution. Please use loc.SET_PROGRAM().")
            raise helper.NeonException("No program specified for execution. Please use neon.SET_PROGRAM().")
        logger.debug(f'SMPC started: {self.id}')

        """Start the computation"""
        arg = helper.join_path_abs(self.config.local_mpspdz_path, protocol.executable)
        if self.config.bits_from_squares and protocol.supports_bits_from_squares:  # and type(protocol) not in [protocol.Yao]:
            # Used the have consistent timings, otherwise timings can jump at 20 parties
            arg += " --bits-from-squares"
        if self.config.batch_size != None and protocol.supports_batch_size:
            arg += f' --batch-size {self.config.batch_size}'
        if self.config.bucket_size != None:
            arg += f' --bucket-size {self.config.bucket_size}'
        if self.prime and protocol.domain == Domain.PRIME:
            arg += f' --prime {self.prime}'
        if self.config.unencrypted:
            logger.critical('ENCRYPTION BETWEEN PARTIES IS DISABLED! THE COMPUTATIONS ARE NOT SECURE.')
            arg += ' --unencrypted '

        if protocol.min_number_of_parties != protocol.max_number_of_parties:
            arg += f" --nparties {amount_of_parties}"
        # Note, "--output-file ." lets all parties print output to stdout
        arg += f" --output-file . " + \
               f" --verbose " + \
               f" --hostname {self.starting_IP}" + \
               f" --player {self.id}" + \
               f" {program}"

        self.last_output = ''
        if self.operation_mode != OperationMode.LOCAL_VIRTUAL and self.config.incoming_bandwidth:
            logger.warning("Incoming Bandwidth restrition not supported in LOCAL and DISTRIBUTED mode!")

        if self.operation_mode == OperationMode.LOCAL:
            # In local operation mode, the latency will have to be enabled only once.
            if self.id == 0:
                self.xio.enable_latency()
        else:
            self.xio.enable_latency()
        self.xio.execute(arg)

    def start_console_output(self):
        """Start to Capture MP-SPDZ output. This has the advantage that we can output MP-SPDZ output live, which is convenient for debugging."""

        # This code just has to be that way :-)

        # Needed to bypass missing "self" within the stream reader
        STDERR = self.stderr = bytearray()
        STDOUT = self.stdout = bytearray()

        def stream_reader(stream, to_stderr: bool):
            tmp_var = to_stderr  # Python won't accept stderr within result otherwise, I don't know why.

            def result():
                for line in stream:
                    if self.id == 0:
                        logger.info(line.decode().strip())
                    if tmp_var:
                        STDERR.extend(line)
                    else:
                        STDOUT.extend(line)
                stream.close()

            return result

        self.__stderr_read_thread = threading.Thread(target=stream_reader(self.xio.process.stderr, True))
        self.__stderr_read_thread.start()
        self.__stdout_read_thread = threading.Thread(target=stream_reader(self.xio.process.stdout, False))
        self.__stdout_read_thread.start()

    def finish_computation(self) -> ClientComputationReport:
        """
        Waits for the MP-SPDZ client to terminate, perform some cleanup operations and creates a ClientComputationReport.
        Returns
        -------
        ClientComputationReport
            The client's report on the computation.
        """
        self.xio.process.wait()
        self.__stderr_read_thread.join()
        self.__stdout_read_thread.join()

        if self.operation_mode == OperationMode.LOCAL:
            # In local operation mode, the latency will have to be disabled only once.
            if self.id == 0:
                self.xio.disable_latency()
        else:
            self.xio.disable_latency()

        stdout = bytes(self.stdout)
        stderr = bytes(self.stderr)

        # Parse the timers, communication metrics and other statistics out of the
        # MP-SPDZ output. Supports both MP-SPDZ 0.4.2 and 0.4.3 (see the function).
        parsed = parse_mpspdz_output(stdout, stderr)

        # Parse private outputs
        private_outputs = None
        binary_output_file = helper.join_path_abs(self.xio.remote_path_to_mpdspz, 'Player-Data',
                                                  f'Binary-Output-P{self.id}-0')
        if self.prime and self.xio.is_file(binary_output_file):
            r = self.xio.read_b(binary_output_file)
            private_outputs = self.read_binary(r)

        self.last_output = self.stdout.decode()

        if self.config.reports_verbosity <= ReportVerbosityLevel.STDOUT:
            self.input = None
            self.ip = None
            private_outputs = None
        if self.config.reports_verbosity <= ReportVerbosityLevel.LOW:
            # also do not log stdout
            stdout = None

        return ClientComputationReport(self.ip,
                                       self.input,
                                       stdout,
                                       stderr,
                                       parsed["cpu_time"],
                                       parsed["total_runtime"],
                                       parsed["data_sent"],
                                       parsed["global_comm_rounds"],
                                       parsed["global_data_sent"],
                                       parsed["offline_phase_time"],
                                       parsed["offline_phase_rounds"],
                                       parsed["offline_phase_data"],
                                       parsed["online_phase_time"],
                                       parsed["online_phase_rounds"],
                                       parsed["online_phase_data"],
                                       parsed["timers"],
                                       parsed["timers_data_sent"],
                                       parsed["timers_comm_rounds"],
                           parsed["timers_lap_data"],
                           parsed["preprocessing_cost"],
                           private_outputs,
                           timers_cpu_time=parsed["timers_cpu_time"],
                           offline_phase_cpu_time=parsed["offline_phase_cpu_time"],
                           online_phase_cpu_time=parsed["online_phase_cpu_time"],
                           setup_time=parsed["setup_time"],
                           communication_details=parsed["communication_details"])

    def get_namespace(self) -> int:
        """The namespaces are from 0-<virtual> with namespace i having ip 172.16.1.(10+i+1) """
        return int(self.ip.split(".")[3]) - 11

    def clean_folders(self):
        """Clean the folders of the respective id."""
        #clean the corresponding folders in the working directory as well!!!
        private_MP = helper.join_path_abs(self.path_to_mpspdz,
                                       "Player-Data/Private-Output-{}".format(self.id))
        self.xio.delete(private_MP)

        public_MP = helper.join_path_abs(self.path_to_mpspdz,
                                      "Player-Data/Public-Output-{}".format(self.id))
        self.xio.delete(public_MP)

        persistent_dir_MP = helper.join_path_abs(self.path_to_mpspdz, "Persistence")
        if os.path.isdir(persistent_dir_MP):
            persistent_MP = helper.join_path_abs(self.path_to_mpspdz,
                                              "Persistence/Transactions-P{}.data".format(self.id))
            if os.path.isfile(persistent_MP):
                os.remove(persistent_MP)
        else:
            os.makedirs(persistent_dir_MP)
        
        private_WD = helper.join_path_abs(self.path_to_mpspdz_copy,
                                       "Player-Data/Private-Output-{}".format(self.id))
        self.xio.delete(private_WD)

        public_WD = helper.join_path_abs(self.path_to_mpspdz_copy,
                                      "Player-Data/Public-Output-{}".format(self.id))
        self.xio.delete(public_WD)

        persistent_dir_WD = helper.join_path_abs(self.path_to_mpspdz_copy, "Persistence")
        if os.path.isdir(persistent_dir_WD):
            persistent_WD = helper.join_path_abs(self.path_to_mpspdz_copy,
                                              "Persistence/Transactions-P{}.data".format(self.id))
            if os.path.isfile(persistent_WD):
                os.remove(persistent_WD)
        else:
            os.makedirs(persistent_dir_WD, exist_ok=True)

    def write_input(self):
        """Write own input to file"""
        #add to workdir as well!!!!!
        input_file_MP = helper.join_path_abs(self.path_to_mpspdz,
                                          "Player-Data/Input-P{}-0".format(self.id))
        self.xio.write(input_file_MP, self.input)

        input_file_WD = helper.join_path_abs(self.path_to_mpspdz_copy,
                                          "Player-Data/Input-P{}-0".format(self.id))
        self.xio.write(input_file_WD, self.input)


    def _convert_montgomery_to_int(self, data: bytes) -> int:
        """Convert motgomery bytes data to int"""
        tmp = int.from_bytes(data, byteorder='little')
        clear = (tmp * self.R_inv) % self.prime
        return clear

    def _convert_int_to_montgomery(self, value: int) -> bytes:
        assert self.prime

        """Convert int to montgomery bytes data"""
        mont = (value * self.R) % self.prime
        # Computes number of bytes for R, which is the minimum length of a single value stored
        nr_bytes = int(math.log2(self.R) // 8)
        data = mont.to_bytes(nr_bytes, byteorder='little')
        return data

    def write_shares(self, shares: List[int]):
        """Write own shares to file"""
        share_file_MP = helper.join_path_abs(self.path_to_mpspdz, f"Persistence/Transactions-P{self.id}.data")

        mp_spdz_transaction_signature = "1F 00 00 00 00 00 00 00 53 68 61 6D 69 72 20 67 66 70 00 10 00 00 00 80 00 " \
                                        "00 00 00 00 00 00 00 00 00 00 00 1B 80 01"
        share_bytes = bytes.fromhex(mp_spdz_transaction_signature)
        for s in tqdm(shares, desc=f"Writing shares for Party {self.id}", leave=None):
            share_bytes += self._convert_int_to_montgomery(s)
        self.xio.write_b(share_file_MP, share_bytes)

        share_file_WD = helper.join_path_abs(self.path_to_mpspdz_copy, f"Persistence/Transactions-P{self.id}.data")

        mp_spdz_transaction_signature = "1F 00 00 00 00 00 00 00 53 68 61 6D 69 72 20 67 66 70 00 10 00 00 00 80 00 " \
                                        "00 00 00 00 00 00 00 00 00 00 00 1B 80 01"
        share_bytes = bytes.fromhex(mp_spdz_transaction_signature)
        for s in tqdm(shares, desc=f"Writing shares for Party {self.id}", leave=None):
            share_bytes += self._convert_int_to_montgomery(s)
        self.xio.write_b(share_file_WD, share_bytes)

    def read_shares(self) -> List[int]:
        """Read the shares and transform them from their montgomery enconding"""
        #altered this one completely to workdir !!!!!!
        share_file = helper.join_path_abs(self.path_to_mpspdz_copy, f"Persistence/Transactions-P{self.id}.data")
        if os.path.exists(share_file):
            data = self.xio.read_b(share_file)
            # Skip the first 39 bytes, as MP-SPDZ writes there some static data
            # See mp_spdz_transaction_signature in write_shares
            return self.read_montgomery(data[39:])
        else:
            return []

    def read_montgomery(self, data: bytes) -> List[int]:
        """Reads montgomery encoded data and converts it to an int"""
        shares = []
        nr_bytes = int(math.log2(self.R) // 8)
        parts = [data[i:i + nr_bytes] for i in range(0, len(data), nr_bytes)]
        for byte in parts:
            # Written Value as integer
            tmp = self._convert_montgomery_to_int(byte)
            shares.append(tmp)
        return shares

    def read_binary(self, data: bytes) -> List[int]:
        """Reads binary encoded data (assumes it is int)"""
        shares = []
        nr_bytes = 8
        parts = [data[i:i + nr_bytes] for i in range(0, len(data), nr_bytes)]
        for byte in parts:
            # Written Value as integer
            tmp = int.from_bytes(byte, byteorder='little')
            shares.append(tmp)
        return shares

    def set_input(self, given_input: str):
        """set the input"""
        self.input = given_input

    @staticmethod
    def is_printing_mpspdz_live() -> bool:
        """
        Returns wheter the Client will print MP-SPDZ's output live during execution.
        """
        return logger.isEnabledFor(logging.DEBUG)
