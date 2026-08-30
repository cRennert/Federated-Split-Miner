import math
import subprocess
import os
import random
import string
from typing import List
from tqdm import tqdm
from multiprocessing.pool import ThreadPool
from multiprocessing import Lock
import time
import fcntl

from .helper import join_path_abs, NeonException

"""This file offloads and abstracts any working with the virtual interfaces. All commands used 
are thoroughly documented and any parameters used for the functions like bas eips should be defined 
in this file or a config, to keep it out of the other NEON files. 
"""
import logging
import logging.config

logging.config.fileConfig(
    join_path_abs(os.path.dirname(__file__), '../config/logging.conf'),
    disable_existing_loggers=False)
# logger = logging.getLogger(__name__)
logger = logging.getLogger("Virtual Network")


class VirtualNetworkManager:
    """The virtual network manager is responsible for creating, expanding and destroying virtual networks.
    If possible, it will try to keep existing virtual networks to improve the performance of NEON."""

    __bridge_active: bool = False
    """Is the network's bridge active?"""
    __current_active_namespaces: int = 0
    """The number of currently existing namespaces."""
    _bridge_ID: str = None
    """ID of the bridge, which should be given to the namespaces as well"""
    __lock_file_path = join_path_abs(os.path.dirname(__file__), 'bridge_ID.lock')

    def initial_setup(self):
        VirtualNetworkManager.clean_previous_neon_namespaces(self)
        self.__setup_bridge()

    def __setup_bridge(self):
        """Creates the initial bridge and its ip necessary for the namespaces"""
        """We introduce a lock file to avoid multiple processes from using creating and using the same ID simultaneously,
           such a case raises Error: Peer netns reference is invalid."""
        retry_interval=0.1 #Time to wait between retries in seconds
        ID_taken = True
        try:
            with open(self.__lock_file_path, "w") as lock_file:
                while True:
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB) #Trying to acquire the lock
                        break  #Lock acquired successfully, exit retry loop
                    except BlockingIOError:
                            time.sleep(retry_interval)  #Wait before retrying
                # Critical section: set_bridge_ID and setup_bridge
                while ID_taken:
                    ID = self.generate_ID()
                    ID_taken = self.is_ID_taken(ID)
                    if not ID_taken:
                        self._bridge_ID = ID
                        print(f"Assigned bridge ID: {ID}") 
                    #if self._bridge_ID is None:
                        #raise ValueError("Bridge ID was not set correctly; aborting bridge setup.")                  
                general = [
                    # Create a bridge
                    f'ip link add name neon_bridge_{self._bridge_ID} txqueuelen 10000 type bridge',

                    # set bridge up
                    f'ip link set neon_bridge_{self._bridge_ID} up',

                    # Give bridge an IP
                    f'ip addr add 172.16.1.10/16 brd + dev neon_bridge_{self._bridge_ID}'
                ]
                # Commands of intereset
                # sysctl -w net.bridge.bridge-nf-call-arptables=0
                # sysctl -w net.bridge.bridge-nf-call-iptables=0
                # sysctl -w net.bridge.bridge-nf-call-ip6tables=0
                # sysctl -w net.bridge.bridge-nf-call-ip6tables=0
                # sysctl -w net.ipv4.icmp_ratelimit=0
                try:
                    VirtualNetworkManager._run_commands(general)
                    self.__bridge_active = True
                except Exception as e:
                    logger.error(e)
                finally:
                    logger.debug("done")
        except IOError as e:
            print(f"Error: Failed to open lock file. {e}")
            return
        finally:
            #Ensure the lock is released even if an error occurs
            if 'lock_file' in locals() and not lock_file.closed:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                except Exception as e:
                    print(f"Warning: Failed to release lock. {e}")

    def setup_network(self, n_parties: int):
        """Set up's the network such that n_parties will be able to perform a computation in that network."""
        if not self.__bridge_active:
            self.__setup_bridge()

        self.namespaces_to_create = list(range(self.__current_active_namespaces, n_parties))
    
        def func_setup_network(i):
            ip = VirtualNetworkManager.get_ip(i)
            self._run_commands([
                # Create a new network namespace nsi
                f'ip netns add neon_ns{i}_{self._bridge_ID}',

                # Create a veth pair to tunnel data into the bridge. vethi is the nsi side, br-vethi the bridge side
                f'ip link add neon_veth{i}_{self._bridge_ID} type veth peer name neon_br_v{i}_{self._bridge_ID}',

                # Move the interface vethi into the namespace nsi
                f'ip link set neon_veth{i}_{self._bridge_ID} netns neon_ns{i}_{self._bridge_ID}',

                # Give the interface an ip depending on the value i
                f'ip netns exec neon_ns{i}_{self._bridge_ID} ip addr add {ip}/24 dev neon_veth{i}_{self._bridge_ID}',

                # Set bridge interface side of tunnel up from default namespace
                f'ip link set neon_br_v{i}_{self._bridge_ID} up',

                # Set the other side of tunnel up from nsi
                f'ip netns exec neon_ns{i}_{self._bridge_ID} ip link set neon_veth{i}_{self._bridge_ID} up',

                # Set bridge br1 as the master of our bridge side interface of the tunnel
                f'ip link set neon_br_v{i}_{self._bridge_ID} master neon_bridge_{self._bridge_ID}',

                # Start local host so party 0 can communicate with itself in neon
                f'ip netns exec neon_ns{i}_{self._bridge_ID} ip link set dev lo up',
            ])
            time.sleep(0.2)  # Adjust delay as needed to reduce race conditions

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_setup_network, self.namespaces_to_create), initial=self.__current_active_namespaces, total=len(self.namespaces_to_create)+self.__current_active_namespaces, desc='Setting up network', leave=None):
                pass


        # We want to verify that the network actually works and also ensure that the bridge's MAC address
        # database is filled. That's why let each host ping all other hosts.
        def func_check_network(party_index):
            for j in range(0, n_parties):
                if party_index == j:
                    continue

                self._run_commands([f'ip netns exec neon_ns{party_index}_{self._bridge_ID} ping -c 1 {VirtualNetworkManager.get_ip(j)} > /dev/null'])

                if j < self.__current_active_namespaces:
                    self._run_commands([f'ip netns exec neon_ns{j}_{self._bridge_ID} ping -c 1 {VirtualNetworkManager.get_ip(party_index)} > /dev/null'])


        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_check_network, self.namespaces_to_create), initial=self.__current_active_namespaces, total=len(self.namespaces_to_create)+self.__current_active_namespaces, desc='Checking network', leave=None):
                pass

        self.__current_active_namespaces = max(self.__current_active_namespaces, n_parties)
        VirtualNetworkManager.check_and_set_neighbor_table_size(self.__current_active_namespaces)

    def tear_down_namespaces(self):
        """Destroys the virtual network completely."""
        VirtualNetworkManager.clean_previous_neon_namespaces(self)
        self.__bridge_active = False
        self.__current_active_namespaces = 0
        self._bridge_ID = None

    def __del__(self):
        VirtualNetworkManager.clean_previous_neon_namespaces(self)

    @staticmethod
    def get_ip(namespace: int) -> str:
        """Returns the IP address of the given client."""
        return f"172.16.{1 + namespace // 245}.{11 + namespace % 245}"

    def generate_ID(self) -> str:
        """Generates a random single-character ID"""
        return random.choice(string.ascii_letters)
    
    def is_ID_taken(self, bridge_id) -> bool:
        tmp_process = subprocess.Popen('ip link show type bridge', shell=True, stdout=subprocess.PIPE)
        out, _ = tmp_process.communicate()

        for line in out.decode().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                bridge_name = parts[1].rstrip(':')
                if bridge_name.startswith('neon_bridge_') and bridge_id in bridge_name:
                    return True
        return False
    
    """def set_bridge_ID(self, lock_timeout=5, retry_interval=0.1):
        #Attempts to set a unique bridge ID with a file-based lock.
        
        #Args:
        #    lock_timeout (float): Maximum time to attempt lock acquisition in seconds.
        #    retry_interval (float): Time to wait between retries in seconds.
        
        start_time = time.time()
        ID_taken = True
        
        while ID_taken:
            try:
                with open(self.__lock_file_path, "w") as lock_file:
                    # Attempt to acquire the lock with retries for `lock_timeout` seconds
                    while True:
                        try:
                            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break  # Lock acquired successfully, exit retry loop
                        except BlockingIOError:
                            # Lock acquisition failed; check for timeout
                            if (time.time() - start_time) >= lock_timeout:
                                print("Timeout: Could not acquire lock within the specified time.")
                                return False  # Exit if lock cannot be acquired
                            else:
                                time.sleep(retry_interval)  # Wait before retrying

                    # Critical section: Generate and set bridge ID
                    ID = self.generate_ID()
                    ID_taken = self.is_ID_taken(ID)
                    if not ID_taken:
                        self._bridge_ID = ID
                        print(f"Assigned bridge ID: {ID}")

            except IOError as e:
                print(f"Error: Failed to open lock file. {e}")
                return False  # Handle file opening error gracefully
            finally:
                # Ensure the lock is released even if an error occurs
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                except Exception as e:
                    print(f"Warning: Failed to release lock. {e}")"""

    @staticmethod
    def clean_previous_neon_namespaces(instance):
        """Searches for NEON-related virtual networks with the corresponding ID and removes them."""        
        ID = instance._bridge_ID
        tmp_process = subprocess.Popen('ip link list', shell=True, stdout=subprocess.PIPE)
        out, _ = tmp_process.communicate()
        bridges = []
        for line in out.decode().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                bridge_name = parts[1].rstrip(':')
                if bridge_name.startswith('neon_bridge_') and bridge_name.endswith(f"_{ID}"):
                    if '@' in bridge_name:
                        at_pos = bridge_name.index('@')
                        bridge_name = bridge_name[0:at_pos]
                    bridges.append(bridge_name)

        logger.debug(f"Bridges left over from previous runs: {bridges}")

        tmp_process = subprocess.Popen('ip netns list', shell=True, stdout=subprocess.PIPE)
        out, _ = tmp_process.communicate()
        namespaces = []
        for line in out.decode().splitlines():
            parts = line.split()
            if len(parts) >= 1:
                namespace = parts[0]
                # Check if the namespace follows the neon_ns{i}_{bridge_id} pattern
                if namespace.startswith('neon_ns') and namespace.endswith(f"_{ID}"):
                    namespaces.append(namespace)

        logger.debug(f"Namespaces left over from previous runs: {namespaces}")

        commands = [f"ip link delete {bridge}" for bridge in bridges]
        commands += [f"ip netns delete {namespace}" for namespace in namespaces]
        VirtualNetworkManager._run_commands(commands)

    @staticmethod
    def check_and_set_neighbor_table_size(nparties: int) -> None:
        max_value = 2 ** math.ceil(math.log2(nparties ** 2))
        safe_values = [max_value // 2, max_value, 2 * max_value]
        commands = [
            f"sysctl -w net.ipv4.neigh.default.gc_thresh{i + 1}={safe_values[i]}" for i in range(3)
        ]

        # Get current values.
        need_to_increase_sizes = False
        for i in range(3):
            tmp_process = subprocess.Popen(f'sysctl -n -b net.ipv4.neigh.default.gc_thresh{i+1}', shell=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = tmp_process.communicate()
            if tmp_process.returncode == 0 and len(out) > 0:
                current_threshold = int(out.decode())
                if current_threshold < safe_values[i]:
                    need_to_increase_sizes = True
                    break
            else:
                logger.warning("Failed to read the size of the neighbor table. This is most likely caused by NEON being run in a container.")
                logger.warning("A too small neighbor table can lead to connectivity issues when more than 40 parties are used.")
                logger.info("If you run into issues, execute the following commands on the host system:")
                for command in commands:
                    logger.info(command)
                return

        if need_to_increase_sizes:
            logger.info("Neighbor table is too small for number of parties.")
            successful = True
            for i in range(3):
                proc = subprocess.run(['sysctl', '-w', f'net.ipv4.neigh.default.gc_thresh{i + 1} = {safe_values[i]}'])
                if proc.returncode != 0:
                    successful = False
                    break

            if not successful:
                logger.critical(
                    'Failed to increase the size of the neighbor table. This might result in connectivity issues.')
                logger.info('To manually increase the size of the neighbor table, run the following commands:')
                for i in range(3):
                    logger.info(f'sysctl -w net.ipv4.neigh.default.gc_thresh{i + 1}={safe_values[i]}')

    @staticmethod
    def _run_commands(commands: List[str], debug=True) -> None:
        """Runs a list of commands and outputs errors if they appear"""
        for command in commands:
            try:
                logger.debug(f'+ Executing command: "{command}"')
                process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate()

                if process.returncode == 0:
                    logger.debug(f'Success: {command}\nOutput: {stdout.decode().strip()}')
                else:
                    logger.error(f"Error executing '{command}': {stderr.decode().strip()}")
                    if debug:
                        raise NeonException(f"Command '{command}' failed with error {stderr.decode().strip()}")

            except Exception as e:
                logger.critical(f"Exception running '{command}': {e}")
                if debug:
                    raise NeonException(f"Exception running '{command}': {e}")

