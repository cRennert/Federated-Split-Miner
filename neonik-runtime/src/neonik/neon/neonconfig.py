import os
from typing import Optional, List
import shutil
import random
import string

from .helper import get_iplist, get_path_to_mpspdz, get_logger, get_cdir, get_path_to_workdir, join_path_abs
logger = get_logger("config")


class ReportVerbosityLevel:
    """Computations reports can get big, especially when many computations with many parties are involved.
    If only limited information is needed, the report verbosity can be used to reduce the size of the computation reports
    and fix corresponding memory issues."""

    #Everything is saved in the computation reports.
    HIGH = 100
    # Same as LOW, but Stdout is additionally saved
    STDOUT = 20
    # Saves only the bare/useful minimum
    LOW = 10
    


class NeonConfig:
    # Variables, can be changed during owncode runtime
    reports_verbosity: ReportVerbosityLevel = ReportVerbosityLevel.LOW
    """The reports verbosity level. Default: LOW"""

    delay: Optional[str] = None
    """Artificial delay to be added to the MPC execution."""
    outgoing_bandwidth: Optional[str] = None
    """Artificial outgoing_bandwidth restrictions to be applied to the MPC execution."""
    incoming_bandwidth: Optional[str] = None
    """Artificial bandwidth restrictions on incoming traffic ON LOCAL-VIRTUAL ONLY."""

    batch_size: Optional[int] = None
    """The value of the batch size parameter of MP-SPDZ."""

    bucket_size: Optional[int] = None
    """Bucket size parameter of MP-SPDZ."""

    bits_from_squares: bool = True
    """The bits-from-squares parameter of MP-SPDZ. Setting this to False might result in inconsistent runtimes from 
        MP-SPDZ."""

    prime: int = 170141183460469231731687303715885907969
    """The prime given to MP-SPDZ. Required for setting and reading persistent memory (secrets)."""

    unencrypted: bool = False
    """DO NOT USE THIS! Disable TLS for the communication between the computing parties. Don't even use this if you have a good reason, the resulting computations will be insecure as heck."""

    local_mpspdz_path: str
    """Path to the local MP-SPDZ instance."""

    compilation_target_path: str
    """Path to the local working directory."""

    working_dir: str

    ssh_key: Optional[str] = None
    ip_list: Optional[List[str]] = None
    remote_path: Optional[str] = None

    @staticmethod
    def copy_directory(source_dir: str, dest_dir: str) -> None:
        try:
            shutil.copytree(source_dir, dest_dir)
            #print(f'Successfully cloned {source_dir} to {dest_dir}')
        except FileExistsError:
            print(f'The directory already exists at {dest_dir}')
        except Exception as e:
            pass
           #print(f'Error occured while cloning the directory: {e}')

    @staticmethod
    def from_environment_vars() -> "NeonConfig":
        result = NeonConfig()

        install_dir = os.getenv("NEONIK_INSTALL_DIR")
        user_dir = os.getenv("NEONIK_USER_DIR")
        if user_dir is None or user_dir == "":
            user_dir = os.getcwd()

        result.working_dir = user_dir
        if len(os.getenv("NEONIK_MPSPDZ_PATH", "")) > 0:
            result.local_mpspdz_path = os.getenv("NEONIK_MPSPDZ_PATH")
        else:
            result.local_mpspdz_path = get_path_to_mpspdz(install_dir)

        random_str = ''.join(random.choices(string.ascii_letters, k=15))
        result.compilation_target_path = get_path_to_workdir(random_str, work_dir=install_dir)
        os.makedirs(result.compilation_target_path, exist_ok=True)

        # Copy the important directories from mpspdz to workdir
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Programs', 'Schedules'),
                                  join_path_abs(result.compilation_target_path, 'Programs', 'Schedules'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Programs', 'Bytecode'),
                                  join_path_abs(result.compilation_target_path, 'Programs', 'Bytecode'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Player-Data'),
                                  join_path_abs(result.compilation_target_path, 'Player-Data'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Persistance'),
                                  join_path_abs(result.compilation_target_path, 'Persistance'))

        result.ssh_key = None
        result.remote_path = None
        result.ip_list = []

        return result

    @staticmethod
    def from_config_files() -> "NeonConfig":
        result = NeonConfig()
        result.working_dir = os.getcwd()
        result.local_mpspdz_path = get_path_to_mpspdz(result.working_dir)
        random_str = ''.join(random.choices(string.ascii_letters, k=15))
        result.compilation_target_path = get_path_to_workdir(random_str)
        os.makedirs(result.compilation_target_path, exist_ok=True)

        #Copy the important directories from mpspdz to workdir
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Programs', 'Schedules'),
                                  join_path_abs(result.compilation_target_path, 'Programs', 'Schedules'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Programs', 'Bytecode'),
                                  join_path_abs(result.compilation_target_path, 'Programs', 'Bytecode'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Player-Data'),
                                  join_path_abs(result.compilation_target_path, 'Player-Data'))
        NeonConfig.copy_directory(join_path_abs(result.local_mpspdz_path, 'Persistance'),
                                  join_path_abs(result.compilation_target_path, 'Persistance'))

        # Read distributed config.
        # with open(os.path.join(get_cdir(), '../config/distributed.conf')) as f:
        #     for line in f.readlines():
        #         parts = line.split('=')
        #         if len(parts) != 2:
        #             continue
        #
        #         if parts[0] == 'folderlocation':
        #             result.remote_path = parts[1].strip()
        #         elif parts[0] == 'sshkey':
        #             result.ssh_key = parts[1].strip()
        # result.ip_list = get_iplist()

        result.ssh_key = None
        result.remote_path = None
        result.ip_list = []

        return result

    def ensure_distribution_mode_compatibility(self, fail_hard=True) -> bool:
        failed = False
        if not self.ssh_key:
            logger.critical("You need to specify a SSH key for distributed mode.")
            failed = True
        if not self.ip_list or len(self.ip_list) == 0:
            logger.critical("You need to specify remote machines for distributed mode.")
            failed = True
        if not self.remote_path:
            logger.critical("You need to specify the remote base path for distributed mode.")
            failed = True
        if failed and fail_hard:
            exit(1)
        return not failed            
    

    def __str__(self) -> str:
        return super().__str__() + f"(delay={self.delay}; outgoing_bandwidth={self.outgoing_bandwidth}; batch_size={self.batch_size};" \
                                   f"bucket_size={self.bucket_size}; bits_from_squares={self.bits_from_squares}; " \
                                   f"prime={self.prime}; mp_spdz={self.local_mpspdz_path}; ssh_key={self.ssh_key}; " \
                                   f"ip_list={self.ip_list}; remote_path={self.remote_path}; compilation_target_path={self.compilation_target_path}; working_dir={self.working_dir})"

