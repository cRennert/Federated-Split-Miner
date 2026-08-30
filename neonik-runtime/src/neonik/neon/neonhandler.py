import atexit
import json
import math
import os
import pprint
import shutil
import subprocess
import sys
import resource
import threading
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union, Callable, Any

from tqdm import tqdm
from multiprocessing.pool import ThreadPool

from .MPSPDZClient import Client  # pylint: disable=E0402
from .MPSPDZClient import logger  # pylint: disable=E0402
from .XIO import LocalVirtualXIO
from .computationreport import ComputationReport, ComputationReportList
from .helper import get_logger, get_mpspdz_version_from_path, \
    rehash_certificates, generate_party_certificate, join_path_abs, NeonException, \
    get_cgroup_peak_memory_in_bytes  # pylint: disable=E0402
from .operationmode import OperationMode
from .programhandler import ProgramHandler  # pylint: disable=E0402
from .shamir import reconstruct_lagrange, generate_shares, generate_shares_insecurely  # pylint: disable=E0402
from .neonconfig import NeonConfig, ReportVerbosityLevel
from .virtualnet import VirtualNetworkManager

from .protocol import Protocol
from . import network

logger = get_logger("Local Handler")

# If more than one NeonHandler exists at any given time, they might interfere with each other, especially
# when using virtual networks. We use this Mutex to prevent such issues.
#NEON_LOCK = Lock()


class NeonHandler:
    """
    The NeonHandler is the center class of NEON. It provides the user interface and orchestrates the flow of the
    secure computations as well as logging.
    """

    operation_mode: OperationMode
    config: NeonConfig

    __program: str
    '''Name of the program executed in the SMPC.'''

    __program_hash: str = None
    '''The hash of the program to be executed.'''

    __pre_substitution_hash: str
    '''The hash of the program to be executed before substitution is applied.'''

    timer_names: Dict[int, str]
    '''The names of the timers as defined by the program.'''

    __protocol: Protocol
    '''Protocol to be used for the computations.'''

    n_parties: int = 0
    '''The number of parties used for a computation.'''

    inputs: List[str] = []
    '''The inputs of the parties'''

    secrets: List[int] = []
    '''Secret states that can be accessed from the executed program using writesharestofile and readsharesfromfile.'''

    __insecure_share_generation: bool = False
    '''DO NOT USE IN PRODUCTION / ONLY FOR RESEARCH: Enables super fast but INSECURE share generation.'''

    __read_secrets: bool = True
    '''Update the secrets after a computation has run.'''

    __find_and_replace: Dict[str, str]

    __print_timers_upon_deletion: bool = True


    __timestamp: str
    __computation_reports: ComputationReportList

    __compiled_programs_path: str
    '''Path to the persistent volume containing the compiled programs.'''

    virtual_network_manager: Optional[VirtualNetworkManager] = None
    '''Do not use unless you know what you are doing: A virtual network manager, that is responsible for configuring 
    virtual networks. Only instanciated in LOCAL_VIRTUAL mode.'''

    currently_computing_clients: Optional[List[Client]] = None
    '''Do not use unless you know what you are doing: A list of the clients that currently running a computation.'''

    def __init__(self, operation_mode: OperationMode, config: NeonConfig):
        # Ensure that only one NeonHandler exists at a time, because multiple NeonHandler at the same time
        # might interfere with each other.
        #NEON_LOCK.acquire(blocking=True)

        self.operation_mode = operation_mode
        self.config = config

        self.__program = ''
        self.__program_hash = ''
        self.__pre_substitution_hash = ''
        self.timer_names = {}
        self.__find_and_replace = {}
        self.__timestamp = str(datetime.now())
        self.__computation_reports = ComputationReportList()
        self.__compiled_programs_path = os.getenv("COMPILED_PROGRAMS_PATH", "/persistent_volume/compiled_programs")

        if operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.virtual_network_manager = VirtualNetworkManager()

        # Setup
        workdir = config.working_dir
        self.ip_list = []
        if config.ip_list:
            self.ip_list = config.ip_list.copy()
        self.program_handler = ProgramHandler(self.config)
        self.log_path = join_path_abs(workdir, "logs", "latest")
        if os.path.isdir(self.log_path):
            try:
                shutil.rmtree(self.log_path)
            except FileNotFoundError:
                pass

        # Logging
        logger.debug(" ".join(sys.argv))
        os.makedirs(join_path_abs(self.log_path), exist_ok=True)

        if operation_mode == OperationMode.LOCAL_VIRTUAL:
            VirtualNetworkManager.clean_previous_neon_namespaces(self.virtual_network_manager)

        atexit.register(self.__final_cleanup)

    #region ############ Main Functions ###############
    def set_number_of_parties(self, number_of_parties: int) -> None:
        """
        Updates the number of parties used for the next secure computation. Updates internal structures like self.inputs
        accordingly.

        Parameters
        ----------
        number_of_parties
            The amount of parties involved in the next computation.
        """

        self.n_parties = number_of_parties

        if len(self.inputs) < number_of_parties:
            self.inputs += ['' for _ in range(number_of_parties - len(self.inputs))]
        elif len(self.inputs) > number_of_parties:
            self.inputs = list(self.inputs[0:number_of_parties])

        assert len(self.inputs) == number_of_parties

    def smpc(self,
             post_launch_hook: Callable[[], None] | None = None,
             post_launch_hook_arguments: tuple[Any,...] | None = None,
             custom_metadata: Any | None = None, 
             skip_compile: bool = False,
             compile_debug = False) -> ComputationReport:
        """Executes a secure computation according to the previously specified parameters.

        Parameters
        ----------
        post_launch_hook
            A functions that gets called after the MP-SPDZ clients were launched. Useful e.g. for external clients connecting to the MP-SPDZ instances.
        post_launch_hook_arguments
            Arguments that can be parsed to the post launch hook
        custom_metadata
            Custom metadata that will be included for in the computation report.
        compile_debug
            Also compile debug output/asm files
        Returns
        ----------
        ComputationReport
            The report on the executed computation.
        """
        if not skip_compile:
            self.compile(compile_debug)

        logger.info('Starting SMPC ' + str(len(self.__computation_reports) + 1) + ' (using ' + str(self.__protocol) + ")")
        clients = self.currently_computing_clients = self.__create_clients()

        if not self.__protocol.min_number_of_parties <= len(clients) <= self.__protocol.max_number_of_parties:
            raise NeonException(f"The computation requires at least {self.__protocol.min_number_of_parties} and at most {self.__protocol.max_number_of_parties} parties.")
            
        self.__prepare_smpc(clients)
        try:
            report = self.__execute_smpc(clients, post_launch_hook, post_launch_hook_arguments, custom_metadata)
        finally:
            pass

        #check if execution was successfull
        if not report.run_was_successfull():
            logger.warning("SMPC execution failed.\n")
        else:
            logger.info('Finished SMPC (successfull).\n')

        # Destroy temporary state
        for client in clients:
            del client.xio
        self.currently_computing_clients = None

        report.max_memory_usage = get_cgroup_peak_memory_in_bytes()
        return report

    def set_input(self, id: int, given_input: str):
        """Set the input of a certain client.

        Parameters
        ----------
        id : int
            Id of the client.
        given_input : str
            New input of the client.
        """
        logger.debug('Set Input of ' + str(id) + '.')
        self.inputs[id] = given_input

    def set_program(self, program: str):
        """Sets the program to be executed. The corresponding program will have to be located in the "Programs" folder.

        Example: We want to execute the program "Programs/example.mpc", we call "neon.SET_PROGRAM('example')".

        Parameters
        ----------
        program : str
            The program to be compiled.
        """
        self.__program = program
        self.__pre_substitution_hash = self.program_handler.get_hash(program, protocol=None).hex()
    #endregion

    #region ############## Helper Functions ##############

    def set_hash(self, program_hash: str):
        """ Sets the program hash """
        self.__program_hash = program_hash

    def set_precompiled_program(self, program: str) -> None:
        """
        Specifies a pre-compiled program to be executed. The compiled binaries must be available to the MP-SPDZ installation
        at config.local_mpspdz_path.
        Parameters
        ----------
        program
            The name of the pre-compiled program.
        """
        self.__program = self.__program_hash = program
        logger.info(f'Pre-compiled program {self.__program} set.')

    def set_substitution(self, find: str, replace: str | int | bool):
        """Before a function evaluation the find value is searched
         in the program and replaced by the replace value this allows to
         use values in the MPSPDZ program that are not necessarily know or
         change during the NEON execution."""
        self.__find_and_replace[find] = str(replace)

    def set_all_inputs(self, given_input: str):
        """Sets the input of all clients to a single string.

        Parameters
        ----------
        given_input : str
            New input of all clients.
        """
        for i in range(self.n_parties):
            self.set_input(i, given_input)

    def set_protocol(self, new_protocol: Protocol):
        """
        Set's the protocol to be used for execution.
        """
        self.__protocol = new_protocol

    def set_network(self, network_setting: network.Network):
        """Set the given artificial bandwidth and delay.
        Set the desired network setting to add artificial delay, and incoming and outgoing bandwidth restrictions.
        The incoming bandwidth restrictions can only be used inf the LOCAL-VIRTUAL SETTING.
        """

        self.config.delay = network_setting.delay
        self.config.incoming_bandwidth = network_setting.incoming_bandwidth
        self.config.outgoing_bandwidth = network_setting.outgoing_bandwidth

    def set_secrets(self, secrets):
        """
        MP-SPDZ allows the clients to store secret values into and later read from a so-called persistent state.
        set_secrets overwrites that persistent state before the next computation with the values provided in the secrets
        parameter. Requires config.prime to be set and the protocol to either be "shamir" or "malicious-shamir".
        Parameters
        ----------
        secrets
            The values to be written to the persistent storage.
        """
        self.secrets = secrets

    def set_insecure_share_generation(self, insecure: bool):
        """
        DO NOT USE IN ANY SETTING IN WHICH SECURITY IS EVEN OF THE SLIGHTEST IMPORTANCE.
        Instead of generating a random polynomial for each secret to be shared, NEON can also use the polynomial
        f(x) = x as basis, resulting in fast, but also SUPER INSECURE secret sharing.

        It is only meant for benchmarking purposes of programs requiring a large secret state.
        """
        self.__insecure_share_generation = insecure

    def set_read_secrets(self, read_secrets: bool):
        """
        Reading the secret state can take a while for big states. With this parameter, you can disable the reading of
        secrets after a computation if you do not require it.
        """
        self.__read_secrets = read_secrets

    def set_batch_size(self, new_batch_size: int):
        """
        Set's MP-SPDZ batch size parameter for the following computations.
        """
        self.config.batch_size = new_batch_size

    def set_bucket_size(self, new_bucket_size: int):
        """
        Set's MP-SPDZ bucket size parameter for the following computations.
        """
        self.config.bucket_size = new_bucket_size

    def set_bits_from_squares(self, bit_from_squares: bool):
        """
        Enables or disables the enforced generation of random bits from squares for the following computations.

        If you want to perform research in dependency with different numbers of parties, you probably want this to be
        set to true. Otherwise MP-SPDZ will switch bit generation algorithms at some points, thus breaking patterns in the data.
        """
        self.config.bits_from_squares = bit_from_squares

    def set_print_timers_upon_deletion(self, print_timers: bool):
        """
        You can use this function to make NEON more/less verbose upon finishing.
        Prints a summary at the very end if set to true.
        Can be helpful or anoying.
        """
        self.__print_timers_upon_deletion = print_timers

    def set_report_verbosity(self, verbosity: ReportVerbosityLevel):
        """Computations reports can get big, especially when many computations with many parties are involved.
        If only limited information is needed, the report verbosity can be used to reduce the size of the computation reports
        and fix corresponding memory issues."""
        self.config.reports_verbosity = verbosity

    def measure_delay(self, from_client_id: Optional[int] = None, to_client_id: Optional[int] = None, slow = False) -> float:
        """
        Measures the delay between two parties using the ping command.
        Parameters
        ----------
        from_client_id
            The id / index of the client executing the ping command. Default: 0
        to_client_id
            The id / index of the client receiving the ping. Default 1
        slow
            If set to true, the delay between individual pings will be higher.

        Returns
        -------
        float
        The measured delay (ping / 2) between the parties.
        """
        from_client, to_client = self.__prepare_network_measurement(from_client_id, to_client_id)

        # Actual measurement.
        command = f'ping -c 5 {to_client.ip}'
        if not slow:
            command += ' -i 0.2'
        out, err = from_client.xio.run_and_wait_for_completion(command)
        if not slow and len(err) > 0 and len(out) == 0:
            command = f"ping -c 5 -i 0,2 {to_client.ip}"
            out, err = from_client.xio.run_and_wait_for_completion(command)
        avg_ping = float(out.decode().splitlines()[-1].split('/')[-3])
        logger.debug(f"Measured a ping of {avg_ping}")

        # Tear down network.
        from_client.xio.disable_latency()
        if self.operation_mode != OperationMode.LOCAL:
            to_client.xio.disable_latency()
        self.currently_computing_clients = None

        return avg_ping / 2

    def measure_bandwidth(self, from_client_id: Optional[int] = None, to_client_id: Optional[int] = None, format: str = 'k', port: int = 14001) -> (float, str):
        """
        Measures the network bandwidth between two clients. REQUIRES ipferf3 TO BE INSTALLED ON ALL INVOLVED MACHINES.
        Parameters
        ----------
        from_client_id
            The client running iperf3 in client mode. Default: 0
        to_client_id
            The cleint running iperf3 in server mode. Default: 1
        format
            The unit of the output, k for kBit, m for mBit, defualt: k

        Returns
        -------
        (float, str)
        (bandwidth, unit)
        """
        # Use a fresh network setup to ensure that previous servers can not interfere. (iperf3 is somewhat strange)
        if self.operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.virtual_network_manager.tear_down_namespaces()
        from_client, to_client = self.__prepare_network_measurement(from_client_id, to_client_id)

        to_client.xio.execute(f'iperf3 --server --one-off --port {port}')
        time.sleep(1)
        from_client.xio.execute(f'iperf3 --client {to_client.ip} --json --format {format} --port {port} --no-delay')
        out, err = from_client.xio.wait()
        to_client.xio.process.kill()
        to_client.xio.process.wait()

        # Tear down network.
        from_client.xio.disable_latency()
        if self.operation_mode != OperationMode.LOCAL:
            to_client.xio.disable_latency()
        self.currently_computing_clients = None

        bits_per_second = json.loads(out.decode())['end']['sum_sent']['bits_per_second']
        bandwidth = round(bits_per_second / 1000)
        bandwidth_unit = 'kbit/sec'

        logger.debug(f"Measured bandwidth of {bandwidth} {bandwidth_unit}")
        return bandwidth, bandwidth_unit

    def multi_bandwidth_measurements(self, many_senders: bool, many_receivers: bool) -> Dict[Tuple[int, int], Tuple[float, str]]:
        """
        Runs multiple simultaneous bandwidth measurements. The parameters determine which kind of measurement should
        be performed. For example, if both many_senders and many_receivers are True, a N-to-N simultaneous measurements
        are done. On the other hand, if many_receivers is false, a N-to-1 measurement is performed.

        You need to specify the number of parties beforehand using set_number_of_parties!

        Parameters
        ----------
        many_senders
        many_receivers

        Returns
        -------

        """
        
        # Increase ulimit for open files (Max seems to be 524288)
        # Default is 1024, but is too low for larger tests
        resource.prlimit(0, resource.RLIMIT_NOFILE, (65536, 65536))
        

        # Prepare the measurements, set up the clients.
        clients = self.currently_computing_clients = self.__create_clients()
        if self.operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.__prepare_virtual_networks(clients)

        if self.operation_mode == OperationMode.LOCAL:
            clients[0].xio.enable_latency()
        else:
            for client in clients:
                client.xio.enable_latency()

        # Start the measurements.
        senders = list(range(self.n_parties)) if many_senders else [0]
        receivers = list(range(self.n_parties)) if many_receivers else [self.n_parties - 1]
        current_port = 14001
        port_mapping = {}
        processes = {}
        
        
        # Start all receivers
        
        # Measure time that it takes to execute command
        start =time.time()
        # Counts how many instances are started (now for receiving. and later for sending)
        counter_starts = 0

        # Do not enable threading, here as it requires timing and state
        # TODO check if this still works
        for receiver in tqdm(receivers, desc='Starting Receivers', leave=None):
            for sender in senders:
                if sender == receiver:
                    continue

                receiving_client = clients[receiver]
                
                receiving_client.xio.execute(f'iperf3 --server --one-off --port {current_port}')
                port_mapping[sender, receiver] = current_port
                current_port += 1
                processes[sender, receiver] = receiving_client.xio.process
                counter_starts += 1


        end = time.time()
        # Time to start an instance, with additional safety margin (start = time for xio.execute, not for iperf3 to start)
        time_offset = 2 * (end-start) / counter_starts
        logger.debug("TIME estimated for starting (xio.execute) all senders (with safety x2): {}".format(counter_starts * time_offset))

        # A small wait to ensure all have started
        time.sleep(1)
        
        # Total Time estimated to start all iperf3
        total_time_esitmated = counter_starts * time_offset
        start = time.time()
        
        # Do not enable threading, here as it requires timing and state
        # TODO check if this still works
        for sender in tqdm(senders, desc='Starting Senders', leave=None):
            for receiver in receivers:
                if sender == receiver:
                    continue
            
                sending_client = clients[sender]
                receiving_client = clients[receiver]
                
                port = port_mapping[sender, receiver]
                
                sleep_time = time_offset * counter_starts
                # Wait for the right time to start
                # Note, with the additional delay (0.1) this means also that the first 0.1 seconds no programm is started
                while (time.time() - start - 0.1) < (total_time_esitmated - sleep_time):
                    pass
                # Add Additional delay for safety reasons
                sleep_time += 1
                sending_client.xio.execute(f" sh -c 'sleep {sleep_time}; iperf3 --client {receiving_client.ip} --json --time 20 --port {port} --no-delay'")
                counter_starts -= 1
                
                rec_proc = processes[sender, receiver]
                processes[sender, receiver] = (sending_client.xio.process, rec_proc)
        
        end = time.time()
        logger.debug(f"TIME actually used to start all senders (should be +0.1 more than estimated): {end - start}")
        
        logger.info("Measuring bandwidth. Please wait.")
        

        # Wait for measurements to finish, parse the results.
        result = {}
        for ((sender, receiver), (sending_process, receiving_process)) in processes.items():
            out, err = sending_process.communicate()
            receiving_process.wait()

            bits_per_second = json.loads(out.decode())['end']['sum_sent']['bits_per_second']
            bandwidth = round(bits_per_second / 1000)
            bandwidth_unit = 'kbit/sec'

            result[sender, receiver] = (bandwidth, bandwidth_unit)

        # Clean up, tear down network.
        if self.operation_mode == OperationMode.LOCAL:
            clients[0].xio.disable_latency()
        else:
            for client in clients:
                client.xio.disable_latency()
        self.currently_computing_clients = None

        return result


    def get_timer_average(self, timer: Union[str, int], execution=-1) -> float:
        """Returns the average time of a timer in a certain execution."""
        l = self.__computation_reports[execution].get_timer_times(int(timer))
        return sum(l) / len(l)

    def get_timer_deviation(self, timer: Union[str, int], execution=-1) -> float:
        """Returns the standart deviation of a timer in a certain execution."""
        l = self.__computation_reports[execution].get_timer_times(int(timer))
        avg = sum(l) / len(l)
        variance = sum([(x - avg) ** 2 for x in l]) / len(l)
        return math.sqrt(variance)

    def get_timer_max(self, timer: int, execution=0) -> float:
        """Returns the max time of a timer in a certain execution."""
        l = self.__computation_reports[execution].get_timer_times(int(timer))
        return max(l)

    def get_last_public_outputs(self) -> Dict[int, str]:
        """gets the output strings of the past smpc session"""
        report = self.__computation_reports[-1]
        return { i: report.client_reports[i].stdout.decode('utf-8') for i in range(len(report.client_reports)) }

    def get_output_of(self, id: int) -> str:
        """Returns last public output of a certain client
        """
        return self.get_last_public_outputs()[id]

    def get_computation_reports(self) -> ComputationReportList:
        """Returns the computation reports of all computations performed so far."""
        return self.__computation_reports

    def save_matplot_figure(self, fig) -> None:
        """Saves a given matplot figure in the current log folder."""
        plot_folder = join_path_abs(self.log_path, 'plots')
        if not os.path.isdir(plot_folder):
            os.makedirs(plot_folder)
        filename_format = join_path_abs(plot_folder, 'plot{0}.png')
        i = 1
        while os.path.isfile(filename_format.format(i)):
            i += 1
        fig.savefig(filename_format.format(i))

    def get_program_hash(self):
        tmp_program = self.__handle_substitutions()
        return self.program_handler.get_hash(tmp_program, self.__protocol).hex()

    def compile(self, compile_debug: bool = False):
        """
        Performs (auto-)substitutions and compiles the program if it has changed.
        """
        # Handle timer naming done in the program
        temp_program = self.__handle_substitutions()
        self.__program_hash = self.program_handler.compile_program_if_necessary(temp_program, self.__protocol, compile_debug)
        
        logger.info('Program "' + self.__program + '" set with hash ' + self.__program_hash)

    def compress_compiled_program(self, outfile: str, compile_if_necessary: bool = True, compile_debug: bool = False):
        if compile_if_necessary:
            self.compile(compile_debug)
        self.program_handler.compress_compiled_program(self.__program_hash, outfile)

    def decompress_compiled_program(self, infile: str):
        self.program_handler.decompress_compiled_program(infile)
    
    def serialize_neon_handler_state(self) -> Dict[str, any]:
        """
            Serializes the state of the neon handler to a dictionary.
            This is useful for saving the state of the neon handler to a file or database.
        """
        return {
            "program": self.program,
            "pre_substitution_hash": self.__pre_substitution_hash,
            "program_hash": self.get_program_hash(),
            "timer_names": self.timer_names,
            "protocol": str(self.protocol),
            "n_parties": self.n_parties,
            "party_inputs": self.inputs,
            "secrets": self.secrets,
            "find_and_replace": self.find_and_replace,
            "compiled_programs_path": self.compiled_programs_path,

            "delay": self.config.delay,
            "incoming_bandwidth": self.config.incoming_bandwidth,
            "outgoing_bandwidth": self.config.outgoing_bandwidth,

            "bits_from_squares": self.config.bits_from_squares,
            "batch_size": self.config.batch_size,
            "bucket_size": self.config.bucket_size,
            "prime": self.config.prime
        }

    @staticmethod
    def deserialize_neon_handler_state(dict: Dict[str, any], operation_mode: OperationMode, neonconfig: NeonConfig) -> tuple["NeonHandler", str]:
        """
            Deserializes the state of the neon handler from a dictionary and return a new NeonHandler instance.
        """
        program_hash = dict["program_hash"]
        timer_names = dict["timer_names"]
        n_parties = dict["n_parties"]
        party_inputs = dict["party_inputs"]
        secrets = dict.get("secrets", None)
        find_and_replace = dict["find_and_replace"]
        compiled_programs_path = dict["compiled_programs_path"]

        protocol_str = dict["protocol"].replace("-", "").lower()
        protocol = Protocol.from_name(protocol_str)

        neonconfig.batch_size = dict["batch_size"]
        neonconfig.bucket_size = dict["bucket_size"]
        neonconfig.bits_from_squares = dict["bits_from_squares"]
        neonconfig.prime = dict["prime"]

        neonconfig.delay = dict["delay"]
        neonconfig.incoming_bandwidth = dict["incoming_bandwidth"]
        neonconfig.outgoing_bandwidth = dict["outgoing_bandwidth"]

        neon = NeonHandler(operation_mode, neonconfig)
        network_setting = network.Network(neonconfig.delay, neonconfig.incoming_bandwidth, neonconfig.outgoing_bandwidth)
        neon.set_network(network_setting)
        neon.set_protocol(protocol)
        neon.set_number_of_parties(n_parties)
        neon.set_batch_size(neonconfig.batch_size)
        neon.set_bits_from_squares(neonconfig.bits_from_squares)
        
        neon.timer_names = timer_names
        neon.set_secrets(secrets if secrets is not None else []) 
        neon.set_hash(program_hash)
        neon.__pre_substitution_hash = dict['pre_substitution_hash']
        neon.set_precompiled_program(program_hash)

        for key, value in find_and_replace.items():
            neon.set_substitution(key, value)

        for i, x in enumerate(party_inputs):
            neon.set_input(i, x)
        
        return neon, program_hash

    #endregion

    #region ############ Programm Logic #################



    def __handle_substitutions(self):
        """
        Performs (auto-)substitutions and returns the temporary program.
        """
        # Handle timer naming done in the program
        self.timer_names, substitutions = self.program_handler.determine_program_timers(self.__program)
        for (key, value) in substitutions.items():
            if key in self.__find_and_replace.keys():
                logger.debug(f"Replacing the substitution for \"{key}\" because of timer definitions by the program.")
            self.__find_and_replace[key] = str(value)

        temp_program = self.program_handler.perform_substitution(self.__program, self.__find_and_replace)
        return temp_program
    
    def __create_clients(self) -> List[Client]:
        assert len(self.inputs) == self.n_parties
        clients = []
        for i in range(self.n_parties):
            # Create client.
            ip = '127.0.0.1'
            if self.operation_mode == OperationMode.DISTRIBUTED:
                if i < len(self.config.ip_list):
                    ip = self.config.ip_list[i]
                else:
                    logger.critical("No more free IPs available")
                    raise NeonException('There are not enough target machines for the computation. Please add target machines in the ip.conf!')
            client = Client(self.operation_mode, self.config, ip, i)

            # Set client parameters.
            client.input = self.inputs[i]

            clients.append(client)
        return clients

    def __prepare_smpc(self, clients: List[Client]):
        """Prepares the files for mpspdz."""
        logger.debug('Preparing SMPC.')
        if self.operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.__prepare_virtual_networks(clients)
        starting_IP = clients[0].ip

        def func_set_and_upload_program_and_inputs(client):
            client.starting_IP = starting_IP
            client.clean_folders()
            self.program_handler.upload_compiled_program(self.__program_hash, self.config, client.xio)
            self.program_handler.upload_complied_program_to_local_workdir(self.__program_hash, self.config, client.xio)
            self.program_handler.upload_complied_program_to_remote_workdir(self.__program_hash, self.config, client.xio)
            client.write_input()
        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_set_and_upload_program_and_inputs, clients), total=len(clients), desc='Set/Upload Program and Inputs', leave=None):
                pass


        # Ensure that enough certificates exist, and generate them otherwise
        # We only generate certificates dynamically in the local/virtual setting
        # In the distributed setting, they are once generated in the beginning, and the uploaded (setup-neon.py prepare-distributed)
        if self.operation_mode != OperationMode.DISTRIBUTED: 
            player_data_path = join_path_abs(self.config.compilation_target_path, "Player-Data")

            # Check if Certificates are missing
            # We only check for the highest number that should be present
            if (not os.path.isfile(join_path_abs(player_data_path, f"P{clients[-1].id}.key"))
                or not os.path.isfile(join_path_abs(player_data_path, f"P{clients[-1].id}.pem"))):

                # TODO check if threading is possible
                for client in tqdm(clients, desc='Check and Generate Missing Certificates', leave=None):
                    if (not os.path.isfile(join_path_abs(player_data_path, f"P{client.id}.key"))
                        or not os.path.isfile(join_path_abs(player_data_path, f"P{client.id}.pem"))):
                        generate_party_certificate(self.config.compilation_target_path, client.id)
                # Rehash Certificates (Required by MP-SPDZ)
                rehash_certificates(self.config.compilation_target_path)

        # distribute secrets only if secrets are set
        if self.secrets:
            self.__distribute_shares(clients)

    def __prepare_virtual_networks(self, clients: List[Client]):
        """Only function that needs to be called before the smpc computation to properly use the virtual networks"""
        self.virtual_network_manager.setup_network(self.n_parties)
        for namespace, client in enumerate(clients):
            client.ip = VirtualNetworkManager.get_ip(namespace)
            assert isinstance(client.xio, LocalVirtualXIO)
            ID = self.virtual_network_manager._bridge_ID
            client.xio.configure_namespace(namespace, ID)

    def __execute_smpc(self,
                       clients: List[Client],
                       post_launch_hook: Optional[Callable[[], None]],
                       post_launch_hook_arguments: Tuple,
                       custom_metadata: Optional[Any] = None) -> ComputationReport:
        """Starts a thread for all current clients to execute SMPC.
        """
        assert len(clients) == self.n_parties

        # Save the secrets before computation for the computation report.
        secrets_before_computation = self.secrets

        # Perform the computation            
        def func_start_client_computations(client):
            client.start_computation(self.n_parties, self.__program_hash, self.__protocol)

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_start_client_computations, clients), total=len(clients), desc='Starting SMPC Parties', leave=None):
                pass
    
        # Manually start the Console output
        # we do this seperately to have cleaner log
        for client in clients:
            client.start_console_output()

        if post_launch_hook:
            if post_launch_hook_arguments:
                post_launch_hook(*post_launch_hook_arguments)
            else:
                post_launch_hook()

        client_computation_reports = []
        for i, client in enumerate(clients):
            client_computation_reports.append(client.finish_computation())

        logger.debug('SMPC finished.')

        if self.__read_secrets and self.config.prime:
            self.__recover_shares(clients)
            secrets_after_computation = self.secrets
        else:
            secrets_after_computation = None

        if self.config.reports_verbosity <= ReportVerbosityLevel.STDOUT:
            secrets_before_computation = None
            secrets_after_computation = None

        # Create the report.
        report = ComputationReport(
            self.__program,
            self.__pre_substitution_hash,
            self.__find_and_replace.copy(),
            self.__program_hash,
            self.timer_names.copy(),
            str(self.__protocol),
            self.operation_mode,
            self.config.delay,
            self.config.outgoing_bandwidth,
            self.config.incoming_bandwidth,
            self.config.bits_from_squares,
            self.config.batch_size,
            self.config.bucket_size,
            get_mpspdz_version_from_path(self.config.local_mpspdz_path),
            client_computation_reports,
            secrets_before_computation,
            secrets_after_computation,
            custom_metadata
        )

        self.__computation_reports.append(report)

        return report

    def __recover_shares(self, clients: List[Client]):
        """Recovers the shares from the clients."""
        shares = []
        if self.config.prime and self.__protocol.supports_secret:
            for client in clients:
                shares.append(client.read_shares())
            self.secrets = reconstruct_lagrange(shares, self.__protocol.get_threshold(len(clients)), self.config.prime)
        else:
            logger.error(f"Secret Reconstruction is not supported for {self.__protocol}")
            self.secrets = []
        logger.debug("The secrets after performing SMPC are " + str(self.secrets))

    def __distribute_shares(self, clients: List[Client]) -> None:
        """Distribute the shares to the clients."""

        if not self.__protocol.supports_secret:
            NeonException(f"Share distribution is not supported for {self.__protocol}")
        
        if not self.config.prime:
            NeonException(f"Set_secret is not supported when config.prime=None.")

        n = len(clients)
        if self.__insecure_share_generation:
            shares = generate_shares_insecurely(n, self.secrets)
        else:
            shares = generate_shares(n, self.__protocol.get_threshold(n), self.secrets, self.config.prime)

        for i, client in enumerate(clients):
            client.write_shares(shares[i])

    def __prepare_network_measurement(self, from_client_id: Optional[int] = None, to_client_id: Optional[int] = None):
        """Performs client and network setup to perform a network measurement."""
        clients = self.currently_computing_clients = self.__create_clients()
        if from_client_id is None or to_client_id is None:
            if len(clients) < 2:
                raise Exception("At least two clients to have joined the computation for delay measurements.")
            from_client_id = 0
            to_client_id = 1

        from_client = clients[from_client_id]
        to_client = clients[to_client_id]

        # Setup network.
        # Store previous virtual active to keep virtual networks alive at the end of the measurement if they were active before.
        if self.operation_mode == OperationMode.LOCAL_VIRTUAL:
            self.__prepare_virtual_networks(clients)
        from_client.xio.enable_latency()
        if self.operation_mode != OperationMode.LOCAL:
            to_client.xio.enable_latency()
        return from_client, to_client

    def __network_cleanup(self):
        if self.currently_computing_clients:
            for client in self.currently_computing_clients:
                del client.xio
            self.currently_computing_clients = None
        if self.virtual_network_manager:
            self.virtual_network_manager.tear_down_namespaces()

    def __final_cleanup(self):
        """Disable latency, bandwidth and virtual networks on shutdown. Also performs some logging for an easier to read output in the shell."""
        """Also delete the working directory"""
        try:
            self.__network_cleanup()
            shutil.rmtree(self.config.compilation_target_path)


            # Print some runtimes to the console.
            for i, report in enumerate(self.__computation_reports):
                if not report.run_was_successfull():
                    logger.warning(f"SMPC {i + 1 : >4} failed. Please check the logs.")
                    continue
                try:
                    if self.__print_timers_upon_deletion:
                        total_runtimes = report.get_total_runtimes()
                        avg_runtime = sum(total_runtimes) / len(total_runtimes)
                        std_runtime = math.sqrt(sum([(x - avg_runtime) ** 2 for x in total_runtimes]) / len(total_runtimes))
                        logger.info(
                            f"SMPC {i + 1 : >4} took on average {avg_runtime} seconds to complete (standart deviation {std_runtime}).")
                        for k in report.get_timer_keys():
                            name = f"{k:>3}"
                            if k in report.timer_names.keys():
                                name = '"' + report.timer_names[k] + '"'
                            logger.info(
                                f"Timer {name} took on average {self.get_timer_average(k, i)} seconds to complete in SMPC {i : >3} (standart deviation {self.get_timer_deviation(k, i)})")
                except Exception as e:
                    logger.critical(e)
        finally:

            # Zip the current report
            try:
                # Find is required because if many log files are created, tar failes because of too many arguments
                # As we want to avoid subdirectories
                logger.warning("Doing Final Cleanup: Compression log files. PLEASE WAIT!")
                tar = subprocess.run('find . -type f -print | tar -cf - -T -', check=True, shell=True, capture_output=True, cwd=self.log_path)
                zst = subprocess.run(['zstd', '-18', '-T0', '-f', '-o',
                                      join_path_abs(self.log_path, '..', self.__timestamp + '.tar.zst')],
                                     check=True, input=tar.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except:
                logger.error("FAILED TO COMPRESS THE LOG FOLDER. Have you installed zstd? Note, you have to backup the logs manually if you need the computation reports in the future.")


    @property
    def protocol(self) -> Protocol:
        return self.__protocol

    @property
    def program(self) -> str:
        return self.__program
    
    @property
    def compiled_programs_path(self) -> str:
        return self.__compiled_programs_path
    
    @property
    def find_and_replace(self) -> Dict[str, str]:
        return self.__find_and_replace

    def __del__(self):
        self.__network_cleanup()
#endregion
