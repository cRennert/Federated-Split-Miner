"""Some people might consider this file the heart piece of NEON. During the runtime of NEON the 
XIO is the only difference between local and distributed execution. Any operarion on the client 
machine(s) is done with abstract commands that are the same in all XIOs but have a different 
program logic. The goal of this is to have the two cases, distributed and local, as close to each other 
as possible and write as little code as needed.

There are actually three XIOs since virtual inherits from local since its is run on the local machine.
The distributed XIO performs the local actions via ssh on its respective client. The qdiscs form 
manipulating the network are quite "moody" so they should run on a normal linux system with appropriate 
rights. 
"""
import logging.config
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Tuple, AnyStr, Optional

from .helper import deconstruct_absolute_path, join_path_abs  # pylint: disable=E0402
from .neonconfig import NeonConfig

logging.config.fileConfig(
    join_path_abs(os.path.dirname(__file__), '../config/logging.conf'),
    disable_existing_loggers=False)
# logger = logging.getLogger(__name__)
logger = logging.getLogger("XIO")

_XIO__DISPLAY_QDISC_ERROR = True


class XIO(ABC):
    config: NeonConfig
    """The NEON-Config used for execution."""

    path_to_mpspdz: str
    """The root directory of MP-SPDZ."""
    path_to_workdir: str
    """The working directory including computation relevant files"""
    delay: Optional[str]
    """Artificial network delay to be applied to the XIO."""
    outgoing_bandwidth: Optional[str]
    """The artificial outgoing bandwidth limitation to be applied to the XIO."""
    network_interface: str
    """The interface on which network limitations are applied."""
    remote_path_to_mpdspz: str
    """The path of the MP-SPDZ binaries within the XIO. Potential use case: uploading of programs."""
    remote_path_to_workdir: str #???

    process: Optional[subprocess.Popen]
    """The currently executed process."""

    def __init__(self, config: NeonConfig, network_interface: str):
        self.config = config
        self.path_to_mpspdz = config.local_mpspdz_path
        self.path_to_workdir = config.compilation_target_path
        self.delay = config.delay
        self.outgoing_bandwidth = config.outgoing_bandwidth
        self.network_interface = network_interface

    # region File System
    @abstractmethod
    def is_file(self, file: str) -> bool:
        """Returns true if the file exists and indeed is a file."""
        pass

    @abstractmethod
    def write_b(self, file: str, data_bytes: bytes) -> None:
        """Writes the given bytes into the given file of the XIO."""
        pass

    @abstractmethod
    def read_b(self, file: str) -> bytes:
        """Reads bytes from the given file of the XIO."""
        pass

    @abstractmethod
    def upload_file(self, local_file: str, xio_file: str) -> None:
        """Uploads a local file into the XIO."""
        pass

    @abstractmethod
    def delete(self, file: str) -> None:
        pass

    def write(self, file: str, data: str) -> None:
        """Writes the given data into the given file on the XIO."""
        self.write_b(file, data.encode('utf-8'))

    def read(self, file: str) -> str:
        """Reads the given file on the XIO as UTF-8 string."""
        return self.read_b(file).decode('utf-8')
    # endregion

    # region Program execution
    @abstractmethod
    def execute(self, command: str) -> None:
        '''Executes the given command in the XIO asynchronously.'''
        pass

    def wait(self) -> Tuple[AnyStr, AnyStr]:
        """Wait till execution is finished

        Returns
        -------
        process.communicate
            Communication object of process.
        """
        return self.process.communicate()

    def run_and_wait_for_completion(self, command: str) -> Tuple[AnyStr, AnyStr]:
        self.execute(command)
        return self.wait()
    # endregion

    # region Latency manipulation
    def enable_latency(self) -> None:
        """Enable nable latency and outgoing_bandwidth restrictions to localhost
        """
        def run_and_check(command: str):
            global __DISPLAY_QDISC_ERROR
            self.execute(command)
            self.process.wait()
            if self.process.returncode != 0 and __DISPLAY_QDISC_ERROR:
                logger.critical(f"Failed to add tc qdisc for artifical outgoing_bandwidth / latency limitations. Please make sure that you are running NEON with root priviledges.")
                __DISPLAY_QDISC_ERROR = False

        if self.outgoing_bandwidth or self.delay:
            set_network_settings = f'tc qdisc add dev {self.network_interface} root netem'
            if self.outgoing_bandwidth:
                logger.debug(f"Add outgoing bandwidth restriction {self.outgoing_bandwidth} to {self.network_interface} ")
                # Space at front required
                set_network_settings += f' rate {self.outgoing_bandwidth}'
            if self.delay:
                logger.debug(f"Add delay {self.delay} to {self.network_interface} ")
                # Space at front required
                set_network_settings += f' delay {self.delay}'
            run_and_check(set_network_settings)


    def disable_latency(self) -> None:
        global __DISPLAY_QDISC_ERROR
        """Disable latency and outgoing_bandwidth restrictions to local host.
        """
        # Check if interface has a qdisc
        self.execute("tc qdisc")
        (out, _) = self.process.communicate()
        if self.network_interface in out.decode('utf-8'):
            try:
                self.run_and_wait_for_completion(f'tc qdisc del dev {self.network_interface} root')
            except Exception as e:
                if __DISPLAY_QDISC_ERROR:
                    logger.warning("Failed to disable local latency and bandwith restrictions. "
                                "If you are not using custom latency/bandwidth, this should not worry you!")
                    __DISPLAY_QDISC_ERROR = False
                else:
                    pass
    # endregion

    def __del__(self):
        """Disable latency is called host on shutdown of system.
        """
        self.disable_latency()


class LocalXIO(XIO):
    """Local XIO class.

    Attributes
    ----------
    """

    def __init__(self, config):
        super().__init__(config, 'lo')
        self.remote_path_to_mpdspz = config.local_mpspdz_path
        self.remote_path_to_workdir = config.compilation_target_path

    def is_file(self, file: str) -> bool:
        return os.path.isfile(file)

    def write(self, file: str, data: str) -> None:
        """Write data to file locally.

        Parameters
        ----------
        file : str
            Path to file.
        data : str
            Data written to file.
        """
        with open(file, 'w') as f:
            f.write(data)

    def write_b(self, file: str, data_bytes: bytes) -> None:
        """Same thing in bytes"""
        with open(file, 'wb') as f:
            f.write(data_bytes)

    def read(self, file: str) -> str:
        """Read data from a given file.

        Parameters
        ----------
        file : str
            Path to file.

        Returns
        -------
        str
            Data read from file.
        """
        try:
            with open(file, 'r') as f:
                return f.read()
        except:
            logger.error("File {} not found".format(file))
            return ""

    def read_b(self, file: str) -> bytes:
        """Same thing in bytes"""
        with open(file, 'rb') as f:
            return f.read()

    def upload_file(self, local_file: str, xio_file: str) -> None:
        #Same file check only occurs if the destination file already exists
        if os.path.exists(xio_file):
            if os.path.samefile(local_file, xio_file):
                return
        shutil.copyfile(local_file, xio_file)
    
    def delete(self, path: str) -> None:
        """Delete a certain file

        Parameters
        ----------
        path : str
            Path of the file.
        """
        if os.path.isfile(path):
            os.remove(path)

    def execute(self, command: str) -> None:
        """Execute MPSPDZ"""
        self.process = subprocess.Popen(command, shell=True, cwd=self.path_to_workdir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def wait(self) -> Tuple[AnyStr, AnyStr]:
        """Wait till execution is finished

        Returns
        -------
        process.communicate
            Communication object of process.
        """
        return self.process.communicate()


class LocalVirtualXIO(LocalXIO):
    """Virtual XIO class.
    Note that here the incoming and outgoing bandwidth restriction is applied to each
    parties incoming and outgoing interface and the delay is 
    only applied to the outgoing interface.

    Attributes
    ----------
    namespace : str
        Number of the neon_ns this xio has.
    prior : str
        Prior appended before commands to put them into the correct namespace.
    """

    prior: Optional[str]
    namespace: Optional[int]
    incoming_bandwidth: Optional[str]
    ID: Optional[str]

    def __init__(self, config: NeonConfig):
        super().__init__(config)
        self.prior = None
        self.namespace = None
        self.network_interface = ""
        self.incoming_bandwidth = config.incoming_bandwidth
        self.ID = None

    def execute(self, arg: str) -> None:
        """Execute MPSPDZ"""
        assert self.prior
        self.process = subprocess.Popen(self.prior + arg, shell=True, cwd=self.path_to_workdir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def configure_namespace(self, namespace: int, ID: str) -> None:
        """Configure the own namespace according to the assigned number"""
        self.namespace = namespace
        self.ID = ID
        self.network_interface = f"neon_veth{namespace}_{ID}"
        self.prior = f"ip netns exec neon_ns{namespace}_{ID} "

    def enable_latency(self) -> None:
        super().enable_latency()
        if self.incoming_bandwidth:
            try:
                logger.debug(f"Add incoming bandwidth restriction {self.incoming_bandwidth} to neon_br_v{self.namespace}_{self.ID} ")
                subprocess.run(f"tc qdisc add dev neon_br_v{self.namespace}_{self.ID} root netem rate {self.incoming_bandwidth}", shell=True, check=True)
            except:
                logger.critical("Cannot set incoming bandwidth restriction.")

    def disable_latency(self) -> None:
        super(LocalVirtualXIO, self).disable_latency()
        if self.incoming_bandwidth:
            # Check if interface has a qdisc
            (out, _) = self.process = subprocess.Popen("tc qdisc", shell=True, cwd=self.path_to_workdir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            # the re.DOTALL is important, as otherwise "." does not capture newlines
            # not also have to check for "qdisc netem" is there, as there are also other lines
            if re.match(f'.*qdisc netem.*neon_br_v{self.namespace}_{self.ID}.*', out.decode('utf-8'), re.DOTALL):
                try:
                    subprocess.run(f"tc qdisc del dev neon_br_v{self.namespace}_{self.ID} root", shell=True, check=True)
                except:
                    logger.critical("Cannot disable incoming bandwidth restriction.")

    def __del__(self):
        """Disable latency is called on shutdown of system.
        """
        self.disable_latency()


class DistributedXIO(XIO):
    """Distributed XIO class.
    """

    """The IP of the target machine."""
    _ip: str

    """A command prefix to be used when executing a command in the remote XIO."""
    _ssh_command: str

    """The path to the MP-SPDZ installation on the remote machine."""
    remote_path_to_mpdspz: str

    remote_path_to_workdir: str

    def __init__(self, config: NeonConfig, ip: str):
        super().__init__(config, "eth0")
        config.ensure_distribution_mode_compatibility(fail_hard=True)
        self._ip = ip
        self._ssh_command_ = f'ssh -i {config.ssh_key} -o "StrictHostKeyChecking no" root@{ip}'

        self.path_to_mpspdz = deconstruct_absolute_path(config.local_mpspdz_path)
        self.remote_path_to_mpdspz = join_path_abs(self.config.remote_path, self.path_to_mpspdz)
        self.path_to_workdir = deconstruct_absolute_path(config.compilation_target_path)
        self.remote_path_to_workdir = join_path_abs(self.config.remote_path, self.path_to_workdir)

    def is_file(self, file: str) -> bool:
        # Not yet tested, but should work
        p = subprocess.Popen(self._ssh_command_ + f' "cd {self.config.remote_path}; test -f {file} && echo yes"', shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out, _) = p.communicate()
        return "yes" in out.decode()

    def write_b(self, file: str, data_bytes: bytes) -> None:
        """Same thing in bytes"""
        # str(data_bytes) + schneide ' ab , ersetze mit "" mache write() -n -e
        # oder datei anlegen und rüber schicken <- hässlich
        # p = subprocess.Popen(self._ssh_command_ + ' echo -n -e '  +'"' +str(data_bytes)[2:-1]+ '"'  +' > ' + '../home' + file.lstrip(".") , shell=True)
        file = deconstruct_absolute_path(file)
        p = subprocess.Popen(self._ssh_command_ + ' "cd ' + self.config.remote_path + ' ; cat -> ' +
                             file + '"', stdin=subprocess.PIPE, shell=True)
        p.stdin.write(data_bytes)
        p.communicate()

    def read_b(self, file: str) -> bytes:
        """Same thing in bytes"""
        file = deconstruct_absolute_path(file)
        p = subprocess.Popen(self._ssh_command_ + ' "cd ' + self.config.remote_path + '; cat ' + file + '"', shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out, _) = p.communicate()
        return out

    def execute(self, arg: str) -> None:
        """Execute MPSPDZ"""
        self.process = subprocess.Popen(
            self._ssh_command_ + f' "cd {self.remote_path_to_workdir} ; {arg}"', #might cause a problem
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def wait(self) -> Tuple[AnyStr, AnyStr]:
        """Wait till execution is finished

        Returns
        -------
        process.communicate
            Communication object of process.
        """
        return self.process.communicate()

    def upload_file(self, local_file: str, xio_file: str) -> None:
        p = subprocess.Popen(f'scp -i {self.config.ssh_key} -o "StrictHostKeyChecking no" {local_file} root@{self._ip}:{xio_file}',
                             stdout=subprocess.DEVNULL, shell=True)
        p.communicate()

    def delete(self, path: str) -> None:
        """Delete a certain file

        Parameters
        ----------
        path : str
            Path of the file.
        """
        # If it does not exists: throws error, process continues.
        p = subprocess.Popen(self._ssh_command_ + ' rm ' + path, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.communicate()

    def __del__(self):
        """Disable latency is called on shutdown.
        """
        self.disable_latency()
