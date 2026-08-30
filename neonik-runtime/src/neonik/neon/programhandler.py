import ast
import hashlib
import json
import os
import os.path
import shutil
import subprocess
import tempfile
import threading
import time
from subprocess import CalledProcessError
from typing import List, Set, Dict
from zipfile import ZipFile, ZIP_LZMA

from .computationreport import ComputationReport
from .XIO import XIO
from .helper import get_cdir, get_logger, join_path_abs, monitor_resources, run_and_capture_output_live  # pylint: disable=E0402
from .neonconfig import NeonConfig
from .protocol import Protocol, Domain

logger = get_logger("Program Handler")


class ProgramHandler:
    """
    The program handlers responsibility is to check for compiled programs / program updates and to upload the compiled programs to the
    executable environments (XIO's).

    It also implements a few quality-of-life improvements, such as a system that only recompiles a program if it
    has changed and thus prevents unnecessary re-compilations or NEON's auto-timer feature which can automatically
    assign timer values to named variables.
    """
    config: NeonConfig
    path_to_local_programs: str

    def __init__(self, config: NeonConfig):
        """Setup the local MPSPDZ directory.
        """
        self.config = config
        self.path_to_local_programs = join_path_abs(config.working_dir, "Programs")

    def compile_program_if_necessary(self, program: str, protocol: Protocol, compile_debug: bool) -> str:
        """Compiles the program locally if necessary and returns the program's hash."""
        # Some are binary circuits only and need different compilation
        hash = self.get_hash(program, protocol).hex()

        if not self.check_program_update(hash, compile_debug):
            # Delete the temporary file
            self.delete_temporary_program(program)
            return hash

        logger.info(f"Compiling program with hash {hash} .")

        # Store CPU and memory usage over time
        cpu_usage = []
        memory_usage = []
        stop_monitoring = threading.Event()

        # Start the monitoring thread
        monitor_thread = threading.Thread(target=monitor_resources, args=(cpu_usage, memory_usage, stop_monitoring))
        monitor_thread.start()

        # Using try-finally block to make sure monitoring stops even if an exception occurs
        try:
            # Compile the program and measure compilation time
            start_time = time.time()
            self.compile_program_locally(self.get_dependencies_for_program(program), program, hash, protocol, compile_debug)
            compilation_time = time.time() - start_time
        finally:
            # Stop the monitoring thread
            stop_monitoring.set()
            monitor_thread.join()

        # Calculate time-weighted CPU and memory utilization
        max_cpu = max(cpu_usage, key=lambda t: t[1])[1]
        max_memory = max(memory_usage, key=lambda t: t[1])[1]
        self.create_measurement_report(max_cpu=max_cpu, max_memory=max_memory, program_hash=hash,
                                       compilation_time=compilation_time)
        return hash

    def compile_program_locally(self, dependencies: Set[str], program: str, program_hash: str, protocol: Protocol,
                                compile_debug: bool):
        """Compiles the program locally."""
        try:
            shutil.copyfile(join_path_abs(self.path_to_local_programs, program + '.mpc'),
                            join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Source', program_hash + '.mpc'))
            # Delete the temporary file
            self.delete_temporary_program(program)
            if len(dependencies) > 0:
                os.makedirs(join_path_abs(self.config.local_mpspdz_path, 'Dependencies'), exist_ok=True)
            for dependency in dependencies:
                os.makedirs(os.path.split(join_path_abs(self.config.local_mpspdz_path, 'Dependencies', dependency))[0], exist_ok=True)
                shutil.copyfile(join_path_abs(self.path_to_local_programs, 'Dependencies', dependency),
                                join_path_abs(self.config.local_mpspdz_path, 'Dependencies', dependency))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location", err, program_hash)
        compiler_file = os.path.abspath(os.path.join(self.config.local_mpspdz_path, "compile.py"))
        try:
            args = [compiler_file, "--papers", "-E", protocol.executable]
            if protocol.domain == Domain.PRIME and self.config.prime is not None:
                args.append("-P " + str(self.config.prime))
            elif protocol.domain == Domain.PRIME:
                args.append("-F 127")
            elif protocol.domain == Domain.BINARY:
                args.append("-B 64")
            elif protocol.domain == Domain.RING:
                args.append("-R 64")
            else:
                raise NotImplementedError("Compiling for other domains not implemented")

            if compile_debug:
                # Extend is required for use of "/" in path name here
                args.extend(["-a", "Programs/asm"])

            args.append(program_hash)
            # Run compilation command and capture the output and the error (if these is one)
            stdout, stderr = run_and_capture_output_live(args, cwd=self.config.local_mpspdz_path)
        except subprocess.CalledProcessError as cpe:
            self.exit_after_error("Cannot compile program", cpe, program_hash)
        except Exception as err:
            self.exit_after_error("Unexpected error during compilation", err, program_hash)

    def create_measurement_report(self, max_cpu: float, max_memory: float, program_hash: str | None = None,
                                  compilation_time: float | None = None, report: ComputationReport | None = None):
        """
            Creates a report with maximal CPU and memory usage and the time taken for compilation.
            The report is stored in the database.
        """
        if compilation_time is not None:
            compilation_report = {
                "program_hash": program_hash,
                "max_cpu_usage": str(max_cpu),
                "max_memory_usage": str(max_memory),
                "compilation_time": str(compilation_time)
            }
            compiled_programs_folder = join_path_abs(self.config.local_mpspdz_path, "Programs")
            filename = os.path.join(compiled_programs_folder, f"compilation_report.json_{time.strftime('%Y%m%d-%H%M%S')}")
            with open(filename, "w") as f:
                json.dump(compilation_report, f, indent=4)
        else:
            report.max_cpu_usage = max_cpu
            report.max_memory_usage = max_memory

    def get_tape_names(self, program_hash: str | None = None, schedule_file: str | None = None) -> list[str]:
        assert (program_hash is None) ^ (schedule_file is None) == 1, "You must provide the program hash or the schedule file, but not both."
        if schedule_file is None:
            schedule_file = join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch')

        with open(schedule_file, 'r') as f:
            # First two lines are parsed, although not realy needed
            num_threads = int(f.readline().strip())
            num_tapes = int(f.readline().strip())
            # The .rsplit(':',1)[0] is required as mp-spdz puts now :xxx at the end of each "filename" inside the schedulefile
            tape_names = [tape_name.rsplit(':', 1)[0] for tape_name in f.readline().strip().split(' ')]
            if len(tape_names) != num_tapes:
                raise Exception('Tape/Schedule format problem')
            return tape_names

    def get_all_files_of_compiled_program(self, program_hash: str, compiled_programs_folder: str | None = None, compile_debug: bool = False):
        if compiled_programs_folder is None:
            compiled_programs_folder = join_path_abs(self.config.local_mpspdz_path, "Programs")

        schedule_file = join_path_abs(compiled_programs_folder, "Schedules", f"{program_hash}.sch")
        tapes = self.get_tape_names(schedule_file=schedule_file)

        result = [schedule_file]
        result.extend([join_path_abs(compiled_programs_folder, 'Bytecode', tape_name + '.bc') for tape_name in tapes])
        if compile_debug:
            result.extend([join_path_abs(compiled_programs_folder, 'Bytecode', f"asm-{tape_name}") for tape_name in tapes])
        # TODO: Public inputs
        return result

    def check_program_update(self, program_hash: str, compile_debug: bool = False):
        """Check if the program needs to be updated"""
        if not os.path.isfile(
                join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Source', program_hash + '.mpc')):
            return True

        try:
            files = self.get_all_files_of_compiled_program(program_hash, compile_debug=compile_debug)
            for file in files:
                if not os.path.isfile(file):
                    return True
        except FileNotFoundError:
            return True
        return False

    def check_program_in_persistent_volume(self, program_hash: str, compiled_programs_path: str, compile_debug: bool) -> bool:
        """Check if the program exists in the persistent volume, if any of the files are missing, return False"""
        if not os.path.isfile(join_path_abs(compiled_programs_path, 'Programs', 'Source', program_hash + '.mpc')):
            return False
        if not os.path.isfile(join_path_abs(compiled_programs_path, 'Programs', 'Schedules', program_hash + '.sch')):
            return False

        schedules_path = join_path_abs(compiled_programs_path, 'Programs', 'Schedules', program_hash + '.sch')
        try:
            tape_names = self.get_tape_names(program_hash, schedules_path)
            for tape_name in tape_names:
                if not os.path.isfile(join_path_abs(compiled_programs_path, 'Programs', 'Bytecode', tape_name + '.bc')):
                    return False
            if compile_debug:
                for tape_name in tape_names:
                    if not os.path.isfile(join_path_abs(compiled_programs_path, 'Programs', 'asm-' + tape_name)):
                        return False
        except Exception as e:
            logger.error(f"Error while checking for compiled program in persistent volume: {e}")
            return False
        return True

    def upload_compiled_program(self, program_hash: str, config: NeonConfig, xio: XIO) -> None:
        try:
            xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch'),
                            join_path_abs(xio.remote_path_to_mpdspz, 'Programs', 'Schedules', program_hash + '.sch'))

            tape_names = self.get_tape_names(program_hash)
            for tape_name in tape_names:
                xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'),
                                join_path_abs(xio.remote_path_to_mpdspz, 'Programs', 'Bytecode', tape_name + '.bc'))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location.", err, program_hash)

    def upload_complied_program_to_local_workdir(self, program_hash: str, config: NeonConfig, xio: XIO) -> None:
        try:
            xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch'),
                            join_path_abs(config.compilation_target_path, 'Programs', 'Schedules', program_hash + '.sch'))
            tape_names = self.get_tape_names(program_hash)
            for tape_name in tape_names:
                xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'),
                                join_path_abs(config.compilation_target_path, 'Programs', 'Bytecode', tape_name + '.bc'))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location.", err, program_hash)

    def upload_complied_program_to_remote_workdir(self, program_hash: str, config: NeonConfig, xio: XIO) -> None:
        try:
            xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch'),
                            join_path_abs(xio.remote_path_to_workdir, 'Programs', 'Schedules', program_hash + '.sch'))

            tape_names = self.get_tape_names(program_hash)
            for tape_name in tape_names:
                xio.upload_file(join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'),
                                join_path_abs(xio.remote_path_to_workdir, 'Programs', 'Bytecode', tape_name + '.bc'))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location.", err, program_hash)

    def compress_compiled_program(self, program_hash: str, outfile: str, compiled_program_folder: str | None = None):
        if compiled_program_folder is None:
            compiled_program_folder = join_path_abs(self.config.local_mpspdz_path, "Programs")

        with ZipFile(outfile, 'w', compression=ZIP_LZMA) as zipf:
            for file in self.get_all_files_of_compiled_program(program_hash, compiled_program_folder):
                zipf.write(file, os.path.relpath(file, compiled_program_folder))
            report_path = os.path.join(compiled_program_folder, "compilation_report.json")
            if os.path.isfile(report_path):
                zipf.write(report_path, os.path.relpath(report_path, compiled_program_folder))
        logger.info(f"Compiled files zipped successfully: {outfile}")

    def decompress_compiled_program(self, infile: str, compiled_program_folder: str | None = None):
        if compiled_program_folder is None:
            compiled_program_folder = join_path_abs(self.config.local_mpspdz_path, "Programs")

        with ZipFile(infile, 'r') as zip_ref:
            zip_ref.extractall(compiled_program_folder)

    def determine_program_timers(self, program: str) -> (Dict[int, str], Dict[str, int]):
        result_timer_names = {}
        variable_name_to_display_name = {}
        autotimers = []
        used_timers = []

        # Go over the program and try to determine the used timers as well as specified timer names.
        with open(join_path_abs(self.path_to_local_programs, program + '.mpc'), 'r') as f:
            for line in f.readlines():
                line = line.lstrip('\t ').rstrip('\n\r')

                if line.lower().startswith('#neon_timer'):
                    # Parse header information
                    parts = line.split(' ')
                    timer = parts[1]
                    name = ' '.join(parts[2:])

                    if timer.isnumeric():
                        # A timer number is given, we shall add it to the data structure.
                        timer = int(timer)
                        result_timer_names[timer] = name
                        used_timers.append(int(timer))
                    else:
                        # A long name for a variable is given, we shall store it for later use.
                        # Cut of the NEON_ if needed.
                        if timer.startswith('NEON_'):
                            timer = timer[5:]
                        variable_name_to_display_name[timer] = name

                elif "start_timer(" in line:
                    # Detect timer usage and autotimers.
                    parts = line.split("start_timer(")
                    parameter = parts[1][:parts[1].index(')')]

                    if parameter.isnumeric():
                        used_timers.append(int(parameter))
                    elif parameter.startswith('NEON_'):
                        autotimers.append(parameter[5:])

        # Determine the first clear timer to be assigned.
        auto_start = 1
        if len(used_timers) > 0:
            auto_start = max(used_timers) + 1

        # Assign timer id's to the auto-timers.
        substitutions = {}
        for (i, name) in enumerate(autotimers):
            timer_id = auto_start + i
            # We do it this way to ensure that only timers are substituted. Also fixes the case in which one timer's name
            # is a prefix of another's timers name.
            substitutions[f"start_timer(NEON_{name})"] = f"start_timer({timer_id})"
            substitutions[f"stop_timer(NEON_{name})"] = f"stop_timer({timer_id})"
            result_timer_names[timer_id] = variable_name_to_display_name[
                name] if name in variable_name_to_display_name.keys() else name

        return (result_timer_names, substitutions)

    def delete_hashes(self, program_hash: str) -> None:
        """In case of a failed compile this delete partial or corrupt files"""
        if not os.path.isfile(
                join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch')):
            return
        files = [join_path_abs('Programs', 'Source', program_hash + '.mpc'),
                 join_path_abs('Programs', 'Source', program_hash + '.sch')]
        tape_names = self.get_tape_names(program_hash)
        for tape_name in tape_names:
            files.append(join_path_abs(self.config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'))
        for tape_name in tape_names:
            files.append(join_path_abs(self.config.local_mpspdz_path, 'Programs', 'asm-' + tape_name))
        for file in files:
            if os.path.isfile(self.config.local_mpspdz_path + file):
                os.remove(self.config.local_mpspdz_path + file)

    def get_hash(self, program: str, protocol: Protocol) -> bytes:
        """This function takes the content of the program and its dependencies and
        creates a hash from this. This hash is used to determine if a program with exactly
        these files has already been compiled and if so, one does not have to newly compile"""
        dependencies = self.get_dependencies_for_program(program)
        logger.info("Program has the following dependencies: {}".format(dependencies))

        with open(join_path_abs(self.path_to_local_programs, program + '.mpc'), 'rb') as f:
            new_code = f.read()

        # Change hash depending on the domain (and possibly other parameters) that it needs
        if protocol:
            new_code += str(protocol.domain.name).encode('utf-8')

        for dependency in sorted(list(dependencies)):
            with open(join_path_abs(self.path_to_local_programs, "Dependencies", dependency), 'rb') as f:
                new_code += f.read()

        return hashlib.blake2s(new_code).digest()

    def get_dependencies_for_program(self, program: str) -> Set[str]:
        """
        Returns all python dependencies for the given program.
        This will likely not work for package like structures, subfolders or similar.
        All imports have to be prefixed by `Dependencies.`

        Parameters
        ----------
        program : str
            Name of the program

        Returns
        -------
        set<str>
            Set of all python dependency file names
        """

        dependencies = set()
        new_files = set()
        new_files.add("../" + program + '.mpc')
        next_round_new_files = set()

        dependendencies_folder = join_path_abs(self.path_to_local_programs, 'Dependencies')

        def try_add(file):
            if os.path.isfile(join_path_abs(dependendencies_folder, file)):
                dependencies.add(file)
                next_round_new_files.add(file)
            # Cut away prefix and filter out all non custom dependencies
            path_parts = file.split(".")
            """if len(path_parts) < 3:
                logger.warning("Malformed import " + file + " (Missing prefix?)")
                # exit(1)
                return"""

            # Check if the file path starts with dependencies (respecting both python . and path seperators) and cust that of in that case.
            tmp1 = path_parts[0].split(os.path.sep)
            if tmp1[0] == "Dependencies":
                tmp1 = tmp1[1:]
            if len(tmp1) > 0:
                path_parts[0] = os.path.join(*tmp1)
            else:
                path_parts = path_parts[1:]
            if len(path_parts) <= 1:
                return

            file = f"{os.path.join(*path_parts[:-1])}.{path_parts[-1]}"
            if os.path.isfile(join_path_abs(dependendencies_folder, file)):
                dependencies.add(file)
                next_round_new_files.add(file)

        while len(new_files) > 0:
            for new_file in new_files:
                parent_dir = os.path.split(new_file)[0]
                # https://stackoverflow.com/questions/9008451/python-easy-way-to-read-all-import-statements-from-py-module
                try:
                    with open(join_path_abs(dependendencies_folder, new_file), 'r') as f:
                        root = ast.parse(f.read(), join_path_abs(dependendencies_folder, new_file))
                except Exception as e:
                    logger.critical("Could not open file " + join_path_abs(dependendencies_folder,
                                                                           new_file) + " or read its dependencies!")
                    logger.error("The error was :\n" + str(e))
                    exit(1)
                for node in ast.walk(root):
                    if isinstance(node, ast.Import):
                        for n in node.names:
                            try_add(n.name + ".py")
                            try_add(os.path.join(parent_dir, f"{n.name}.py"))
                    elif isinstance(node, ast.ImportFrom):
                        try_add(node.module + ".py")
                        try_add(os.path.join(parent_dir, f"{node.module}.py"))

            new_files = next_round_new_files
            next_round_new_files = set()

        return dependencies

    def get_compiled_program(self, program_hash: str, compiled_programs_path: str) -> List[str]:
        """Returns the path to the compiled program"""
        if self.check_program_in_persistent_volume(program_hash, compiled_programs_path, False):
            source_path = join_path_abs(compiled_programs_path, 'Programs', 'Source', program_hash + '.mpc')
            schedules_path = join_path_abs(compiled_programs_path, 'Programs', 'Schedules', program_hash + '.sch')
            tape_names = self.get_tape_names(program_hash, schedules_path)
            bytecode_path_list = []
            for tape_name in tape_names:
                bytecode_path = join_path_abs(compiled_programs_path, 'Programs', 'Bytecode', tape_name + '.bc')
                bytecode_path_list.append(bytecode_path)
            return [source_path, schedules_path] + bytecode_path_list
        else:
            return None

    def copy_compiled_program_to_local_mpspdz(self, program_hash: str, path_list: List[str], config: NeonConfig) -> None:
        """
            Copies the compiled program from the persistent volume to the local MPSPDZ directory
            path_list[0] = source file
            path_list[1] = schedule file
            path_list[2:] = bytecode files
        """
        try:
            shutil.copyfile(path_list[0], join_path_abs(config.local_mpspdz_path, 'Programs', 'Source', program_hash + '.mpc'))
            shutil.copyfile(path_list[1], join_path_abs(config.local_mpspdz_path, 'Programs', 'Schedules', program_hash + '.sch'))
            tape_names = self.get_tape_names(program_hash, path_list[1])
            for (bytecode_path, tape_name) in zip(path_list[2:], tape_names):
                shutil.copyfile(bytecode_path, join_path_abs(config.local_mpspdz_path, 'Programs', 'Bytecode', tape_name + '.bc'))
        except Exception as err:
            self.exit_after_error("Cannot copy program to destination location.", err, program_hash)

    def perform_substitution(self, program: str, find_and_replace: Dict[str, str]) -> str:
        """Performs the substituions that were set with set_substitution, returns the filename of the resulting temporary program."""

        # create temp file
        file_handler_id, temp_file = tempfile.mkstemp(suffix=".mpc", dir=self.path_to_local_programs)
        # Manually close the file. Must be done, as otherwise the temporary file will be always open, and thus can cause the "too many open files" error.
        os.close(file_handler_id)
        # Find and replace
        with open(os.path.join(self.path_to_local_programs, program + '.mpc'), 'r') as f:
            filedata = f.read()
        for k, v in find_and_replace.items():
            filedata = filedata.replace(k, v)
        with open(temp_file, 'w') as f:
            f.write(filedata)
        # return the name of  the temporary file
        return temp_file.split('/')[-1].split('.')[0]

    def delete_temporary_program(self, temp_program: str):
        """delete the temporary program from the substitution after it has been compiled"""
        os.remove(join_path_abs(self.path_to_local_programs, temp_program + '.mpc'))

    def exit_after_error(self, error, original_error, program_hash: str):
        """In case of an error this function gives feedback and deletes
        any corrupt files"""
        logger.exception(original_error)
        logger.critical(error)
        self.delete_hashes(program_hash)
        raise original_error