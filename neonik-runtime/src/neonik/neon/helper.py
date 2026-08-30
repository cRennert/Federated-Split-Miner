"""This file defines some helping functions to do montgomery-int conversions.
Any math related helping function could belong in here.
Also any small helping funcions that are called from several other classes are
present here to have a better maintainability.
"""
import math
import pathlib
import os
import re

import logging
import logging.config

import threading
import time
import traceback
from typing import List, Tuple, Optional

import subprocess
import atexit


# Exceptiong for NEON
class NeonException(Exception):
    pass

def join_path_abs(*list_of_paths) -> str:
    """Join Pathes and build the absolute path"""
    return os.path.abspath(os.path.join(*list_of_paths))

def get_logger(name: str) -> logging.Logger:
    # Check if debug logging config file exists, otherwise use the default config file
    config_dir = join_path_abs(os.path.dirname(__file__), '..', 'config')
    config_filename = join_path_abs(config_dir, 'debug_logging.conf')
    if not os.path.isfile(config_filename):
        config_filename = join_path_abs(config_dir, 'logging.conf')

    logging.config.fileConfig(config_filename, disable_existing_loggers=False)
    return logging.getLogger(name)


logger = get_logger("Helper")

class PopenWithExit:
    """
    Just a wrapper around subprocess.Popen that add a cleanup method to ensure the process is exited in case of an abort.
    """

    def __init__(self, *args, **kwargs):
        self.process = subprocess.Popen(*args, **kwargs)
        # Register Cleanup
        atexit.register(self.__cleanup)

    def __cleanup(self):
        # Check if it is still running (if yes it returns None)
        if self.process.poll() is None:
            logger.debug("Cleaning up left over processes.")
            try:
                # First try a graceful terminate and wait up to one second
                self.process.terminate()
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                # Othrewise just kill it
                self.process.kill()

    def poll(self):
        return self.process.poll()

    def wait(self):
        return self.process.wait()

    def terminate(self):
        # Deregister Cleanup
        atexit.unregister(self.__cleanup) 
        return self.process.terminate()

    def communicate(self, *args, **kwargs):
        # Deregister Cleanup
        atexit.unregister(self.__cleanup)
        return self.process.communicate(*args, **kwargs)


def deconstruct_absolute_path(path: str) -> str:
    """ Used to go from an absolute path back to a relative one."""
    return "./temp" + path.split("temp")[-1]


def get_cdir() -> str:
    """Get the current directory of this file. This allows the whole code
    to be relative to this file and not dependent on the calling directory"""
    return str(pathlib.Path(__file__).parent.absolute())


def get_workdir_or_default_workdir(workdir: str | None = None) -> str:
    if workdir:
        return workdir
    return join_path_abs(get_cdir(), "..", "..")


def major_minor_micro(version: str) -> Tuple[int, int, int]:
    """Find the highest version in a given list"""
    major, minor, micro = re.search(r'(\d+)\.(\d+)\.(\d+)', version).groups()

    return int(major), int(minor), int(micro)


def create_version_list(zip_path: str) -> List[str]:
    """Creates a list of versions"""
    versions = []
    for filename in os.listdir(zip_path):
        versions.append(filename)
    return versions


def find_value_from_config(config_file: str, key: str) -> Optional[str]:
    result = None
    with open(config_file, 'rt') as f:
        for line in f.readlines():
            parts = line.split('=')
            if len(parts) == 2 and parts[0] == key:
                result = parts[1]
    return result

def get_iplist(workdir: str | None = None) -> List[str]:
    """Reads the iplist from ip.conf and returns it"""
    with open(join_path_abs(get_workdir_or_default_workdir(workdir), "config", "ip.conf"), 'r') as f:
        ip_list = f.read().splitlines()
    return ip_list

def get_path_to_temp(work_dir: str | None = None) -> str:
    """Get the path to the temp directory"""
    return join_path_abs(get_workdir_or_default_workdir(work_dir), 'temp')

def get_path_to_mpspdz(work_dir: str | None = None) -> str:
    """Get the path to the newest folder of MPSPDZ in the temp directory"""
    search_dir = join_path_abs(get_path_to_temp(work_dir), 'MP')
    return join_path_abs(search_dir, os.listdir(search_dir)[0])

def get_path_to_workdir(index: str, work_dir: str | None = None) -> str:
    """Get the path to the working directory"""
    return join_path_abs(get_path_to_temp(work_dir), f'WorkDir_{index}')

def mod_inverse(a: int, modulus: int) -> int:
    """Computes the inverse of a mod modulus"""
    return pow(a, -1, modulus)


def get_R_for_prime(prime: int) -> int:
    """
        R is the minimal power of 2^64 larger than prime
        """
    bits_prime = math.log2(prime)
    power_r = math.ceil(bits_prime / 64)
    r = 2 ** (64 * power_r)
    return r


def weighted_avg(list: List[Tuple]):
    su = 0.0
    st = 0.0
    last_u = None
    last_t = None
    for (t, u) in list:
        if last_u is None:
            last_u = u
            last_t = t
        else:
            delta_t = t - last_t
            su += last_u * delta_t
            st += delta_t
            last_u = u
            last_t = t
    if st == 0.0:
        return 0
    else:
        return su / st


def to_bytes(f: float) -> bytes:
    """Turn a float into an int and then into bytes"""
    f = round(f)
    return f.to_bytes((f.bit_length() + 7) // 8, byteorder='big')


def get_mpspdz_version_from_path(mpspdz_path: str) -> str:
    return os.path.split(mpspdz_path)[-1].split('-')[-1]


def rehash_certificates(path_to_mpspdz: str):
    subprocess.run(['c_rehash', 'Player-Data'], cwd=path_to_mpspdz, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    
def generate_party_certificate(path_to_mpspdz: str, party_id: int):
    key_file = join_path_abs(path_to_mpspdz, "Player-Data", f"P{party_id}.key")
    cert_file = join_path_abs(path_to_mpspdz, "Player-Data", f"P{party_id}.pem")
    subprocess.run(['openssl', 'req', '-newkey', 'rsa',
                    '-nodes', '-x509', '-out',
                    cert_file,
                    '-keyout', key_file, '-days', '1000000', '-subj', f'/CN=P{party_id}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def average_and_standard_deviation(values: List[float]) -> (float, float):
    avg = sum(values) / len(values)
    std = math.sqrt(sum([(x - avg) ** 2 for x in values]) / len(values))
    return avg, std


def min_median_max(values: List[float]) -> (float, float, float):
    copy = values.copy()
    copy.sort()
    return copy[0], copy[len(copy) // 2], copy[-1]

def monitor_resources(cpu_usage: List[float], memory_usage: List[float], stop_monitoring):
    import psutil
    """
        Continuously monitors CPU and memory usage during compilation.
    """
    root_process = psutil.Process()  #This is the current process
    while not stop_monitoring.is_set():
        timestamp = time.time()
        cpu = psutil.cpu_percent(interval=None)
        mem_in_bytes = get_process_tree_memory_usage(root_process)
        mem = mem_in_bytes / (1024 * 1024) # Convert bytes to MB
        cpu_usage.append((timestamp, cpu))
        memory_usage.append((timestamp, mem))
        time.sleep(0.05)

def get_process_tree_memory_usage(root_process: psutil.Process) -> float:
    import psutil
    """
        Return total RSS memory (in bytes) used by a process and all its children.
    """
    try:
        children = root_process.children(recursive=True)
        all_procs = [root_process] + children
        return sum(p.memory_info().rss for p in all_procs if p.is_running())
    except psutil.NoSuchProcess:
        return 0

def trace_subprocesses(interval=0.1):
    import psutil
    root = psutil.Process()
    seen = set()

    while True:
        try:
            children = root.children(recursive=True)
            for child in children:
                if child.pid not in seen:
                    print(f"[{time.strftime('%X')}] Spawned PID {child.pid}: {child.cmdline()}")
                    seen.add(child.pid)
            time.sleep(interval)
        except psutil.NoSuchProcess:
            break


def get_cgroup_peak_memory_in_bytes() -> int | None:
    try:
        with open(os.path.join("/proc", "self", "cgroup"), "r") as f:
            cgroup = f.readline().strip()[3:].lstrip("/")
        with open(os.path.join("/sys", "fs", "cgroup", cgroup, "memory.peak"), "r") as f:
            return int(f.readline())
    except:
        traceback.print_exc()
        return None
    
def run_and_capture_output_live(command, cwd=None):
    """
        Runs a subprocess with live logging and returns the captured output. This is necessary for printing the compilation outputs and errors when implementing locally.
    """
    stdout_buffer = []
    stderr_buffer = []

    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    def read_stream(stream, buffer):
        """
            Reads a stream line by line and appends the lines to a buffer.
        """
        for line in stream:
            print(line, end='', flush=True)
            buffer.append(line)

    # Start threads to read stdout and stderr, wait for the process to complete and capture the output
    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_buffer))
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_buffer))

    stdout_thread.start()
    stderr_thread.start()
    process.wait()
    stdout_thread.join()
    stderr_thread.join()

    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command, output=''.join(stdout_buffer), stderr=''.join(stderr_buffer))

    return ''.join(stdout_buffer), ''.join(stderr_buffer)