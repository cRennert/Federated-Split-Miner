#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from tempfile import NamedTemporaryFile, TemporaryDirectory, mkdtemp
from typing import List
import requests

from tqdm import tqdm, trange
from multiprocessing.pool import ThreadPool

from neonik.neon.helper import get_cdir, get_logger, get_path_to_mpspdz, generate_party_certificate, rehash_certificates, join_path_abs, NeonException, get_path_to_temp
from neonik.neon.neonconfig import NeonConfig

logger = get_logger('Setup')


class SetupCommandLineInterface:
    def __init__(self):
        allowed_commands = ['install-mpspdz', 'prepare-distributed', 'clean-virtual']
        parser = argparse.ArgumentParser(
            description='Setup and management script for NEON.',
            usage=f'''./setup-neon.py <command> [args]
            
Available commands: {', '.join(allowed_commands)}
            '''
        )
        parser.add_argument('command')
        args = parser.parse_args(sys.argv[1:2])
        command: str = args.command
        command_function_name = command.replace('-', '_')
        if command not in allowed_commands or not hasattr(self, command_function_name):
            logger.critical(f'Unrecognized command: {command}')
            parser.print_help()
            return

        getattr(self, command_function_name)(sys.argv[2:])

    def _download_mpspdz_release(self, version: str, path_to_mpspdz: str):
        # Fetch the version id of the latest version if necessary.
        if version == 'latest':
            version = requests.get("https://api.github.com/repos/data61/MP-SPDZ/releases/latest").json()["tag_name"][1:]

        logger.info(f"Installing MP-SPDZ version {version}")

        extraction_folder = mkdtemp(prefix="mp-spdz")
        if len(os.getenv("NEONIK_SETUP_CACHE", "")) > 0:
            extraction_folder = os.path.abspath(os.path.join(os.getenv("NEONIK_SETUP_CACHE", ""), f"mp-spdz-{version}"))
            if os.path.isdir(extraction_folder):
                print(f"Using already downloaded MP-SPDZ version in {extraction_folder}")
                actual_mp_spdz_path = os.path.join(extraction_folder, os.listdir(extraction_folder)[0])
                shutil.copytree(actual_mp_spdz_path, path_to_mpspdz)
                return


        # Download the precompiled MP-SPDZ version.
        url = f"https://github.com/data61/MP-SPDZ/releases/download/v{version}/mp-spdz-{version}.tar.xz"

        with NamedTemporaryFile(prefix="mpspdz-download") as local_file:
            with requests.get(url, stream=True) as r:
                shutil.copyfileobj(r.raw, local_file)
            local_file.flush()

            # Extract the downloaded ZIP into the installation folder.
            logger.info(f"Decompressing {local_file} please be patient...")
            with tarfile.open(local_file.name) as f:
                f.extractall(extraction_folder)
            
            actual_mp_spdz_path = os.path.join(extraction_folder, os.listdir(extraction_folder)[0])
            print(actual_mp_spdz_path)
            shutil.copytree(actual_mp_spdz_path, path_to_mpspdz)

    def _patch_mpspdz_for_toolchain(self, path_to_mpspdz: str):
        """Patch Makefile and CONFIG to work with system Boost >=1.85 and clang >=18."""
        makefile_path = os.path.join(path_to_mpspdz, "Makefile")
        with open(makefile_path, 'r') as f:
            content = f.read()
        old = "OTE_OPTS += -DENABLE_SOFTSPOKEN_OT=ON -DCMAKE_CXX_COMPILER=$(CXX) -DCMAKE_INSTALL_LIBDIR=lib"
        new = old + " -DBoost_NO_SYSTEM_PATHS=ON -DBOOST_ROOT=$(CURDIR)/local"
        if old in content and new not in content:
            content = content.replace(old, new)
            with open(makefile_path, 'w') as f:
                f.write(content)
            logger.info("Patched Makefile: force local Boost for libOTe build")

        config_path = os.path.join(path_to_mpspdz, "CONFIG")
        with open(config_path, 'r') as f:
            content = f.read()
        marker = "CFLAGS += $(BREW_CFLAGS)"
        patch_line = "CFLAGS += -Wno-error=deprecated-literal-operator\n"
        if marker in content and patch_line not in content:
            content = content.replace(marker, patch_line + marker, 1)
            with open(config_path, 'w') as f:
                f.write(content)
            logger.info("Patched CONFIG: suppress deprecated-literal-operator warning")

    def _download_and_compile_mpspdz_from_git(self, path_to_mpspdz: str, protocols: list[str]):
        parent_folder = os.path.split(os.path.abspath(path_to_mpspdz))[0]
        # TODO: Switch to workdir
        logger.info("Downloading MP-SPDZ version: git")
        subprocess.run("git clone https://github.com/data61/MP-SPDZ.git", shell=True, cwd=parent_folder)
        shutil.move(os.path.join(parent_folder, "MP-SPDZ"), path_to_mpspdz)
        self._patch_mpspdz_for_toolchain(path_to_mpspdz)
        nr_cpu_cores = os.cpu_count()
        tools_to_compile = ["boost", "tldr"]

        if not protocols:
            # Make all protocols
            tools_to_compile.append("")
        else:
            tools_to_compile.extend(f"{p}-party.x" for p in protocols)

        for to_make in tools_to_compile:
            if to_make:
                logger.info(f"Building {to_make}. Please wait")
            else:
                logger.info(f"Building all protocols. Please wait")
            subprocess.run(f"make -j {nr_cpu_cores} {to_make}", shell=True, cwd=path_to_mpspdz, check=True)

    def install_mpspdz(self, args: List[str]):
        parser = argparse.ArgumentParser(description='Update the local MP-SPDZ version.')
        parser.add_argument('--version', type=str, default='latest', dest='version',
                            help='The MP-SPDZ-version to be installed.')
        parser.add_argument('--protocols', '--protocol', type=str, dest='protocols',
                            help='Requires "--version git", comma- or space-separated protocols to compile (e.g. replicated-ring,sy-rep-ring). If not specified, all are compiled')
        parser.add_argument('--target-location', type=str, help="The path MP-SPDZ should be installed to.", dest="path_to_mpspdz",
                            default=os.getenv("NEONIK_MPSPDZ_PATH"))
        parsed_args = parser.parse_args(args)
        version = parsed_args.version
        path_to_mpspdz = os.path.abspath(parsed_args.path_to_mpspdz)

        neon_working_dir = os.getcwd()

        logger.debug(f'Installing, args={args}')

        # TODO: Check if still needed. 
        # p = subprocess.Popen("command -v zstd", shell=True,
        #                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # out, err = p.communicate()
        # if "zstd" not in out.decode():
        #     logger.critical("zstd is not installed. Please install it first.")
        #     return

        # Remove prior MP-SPDZ installations.
        if os.path.isdir(path_to_mpspdz):
            shutil.rmtree(path_to_mpspdz)
        if version == 'git':
            raw_protocols = parsed_args.protocols or ""
            protocols = [p.strip() for p in raw_protocols.replace(",", " ").split() if p.strip()]
            self._download_and_compile_mpspdz_from_git(path_to_mpspdz, protocols)
        else:
            self._download_mpspdz_release(version, path_to_mpspdz)

        # Setup the new MP-SPDZ installation.
        if version != 'git':
            logger.info("Finalizing MP-SPDZ install.")
            subprocess.run('Scripts/tldr.sh', shell=True, cwd=path_to_mpspdz, stdout=subprocess.DEVNULL)
        
        for folder in [
            join_path_abs(neon_working_dir, "Programs", "Dependencies"),
            join_path_abs(path_to_mpspdz, "Persistence"),
            join_path_abs(path_to_mpspdz, "Player-Data"),
            join_path_abs(path_to_mpspdz, "Programs", "Schedules"),
            join_path_abs(path_to_mpspdz, "Programs", "Bytecode")
        ]:
            os.makedirs(folder, exist_ok=True)
            

    def prepare_distributed(self, args: List[str]):
        parser = argparse.ArgumentParser(description='Prepares remote machines to be used as MP-SPDZ clients for computations.')
        parser.add_argument('--sshkey', type=str, default=None, dest='ssh_key',
                            help='The SSH key to be used when connecting to the remote machines.')
        args = parser.parse_args(args)

        # Test if the installation has run before.
        cdir = join_path_abs(get_cdir(), '..')
        if not os.path.isdir(join_path_abs(cdir, 'temp')):
            logger.critical('You need to install MP-SPDZ locally first.')
            logger.critical('Please run "./setup-neon.py install-mpspdz')
            return

        # Prepare the neon-config.
        config = NeonConfig.from_config_files()
        if args.ssh_key:
            config.ssh_key = args.ssh_key
        config.ensure_distribution_mode_compatibility(fail_hard=True)

        remote_machine_without_zstd = False
        for remote_machine in config.ip_list:
            command_template = 'ssh -i ' + config.ssh_key + ' -o "StrictHostKeyChecking no"' + ' root@' + \
                               remote_machine + ' "{}"'

            # Check if zstd is installed.
            p = subprocess.Popen(command_template.format("command -v zstd"), shell=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = p.communicate()
            if "zstd" not in out.decode():
                # Install zstd if necessary.
                logger.critical(f"zstd not installed on remote machine {remote_machine}. Please install zstd first.")
                remote_machine_without_zstd = True
        if remote_machine_without_zstd:
            return


        path_to_mpspdz = get_path_to_mpspdz()
        # TODO check if this can be made threaded
        for cert_number in trange(len(config.ip_list), desc="Generating Certificates"):
            generate_party_certificate(path_to_mpspdz, cert_number)
        rehash_certificates(path_to_mpspdz)


        # Compress the data to be uploaded to the target machines.
        logger.info("Please wait, while tar archive is being created. This may take a while!")
        F = './temp/'
        Fzip = './temp.tar.zst'
        subprocess.run('tar -cf - ' + F + '/ | zstd -18 -T0 -f -o ' + Fzip, shell=True, cwd=cdir)

        def func_prepare_remote_machine(remote_machine):
            command_template = 'ssh -i ' + config.ssh_key + ' -o "StrictHostKeyChecking no"' + ' root@' + \
                               remote_machine + ' "{}"'

            # Delete old files
            subprocess.run(command_template.format(f"rm -rf {config.remote_path}"), shell=True)
            
            # Prepare upload destination.
            subprocess.run(command_template.format(f"mkdir -p {config.remote_path}"), shell=True)

            # Upload the data.
            subprocess.run(f'scp -i {config.ssh_key} {Fzip} root@{remote_machine}:{config.remote_path}', shell=True, cwd=cdir, stdout=subprocess.DEVNULL)

            # Decompress the uploaded data.
            subprocess.run(command_template.format(f"cd {config.remote_path} ; tar -I zstd -xf temp.tar.zst"), shell=True)
            
            # Delete tar archive
            subprocess.run(command_template.format(f"rm -rf {config.remote_path}/temp.tar.zst"), shell=True)

        with ThreadPool() as pool:
            for i in tqdm(pool.imap_unordered(func_prepare_remote_machine, config.ip_list), total=len(config.ip_list), desc="Preparing remote hosts"):
                pass
        
        # Delete tar archive
        subprocess.run(f"rm {Fzip}", shell=True, cwd=cdir)

    def clean_virtual(self, args: List[str]):
        from neonik.neon.virtualnet import VirtualNetworkManager
        # TODO: Fix or completly remove this.
        VirtualNetworkManager.clean_previous_neon_namespaces()
        logger.info("Removed previous virtual namespaces.")
        

def main():
    # For UV script
    SetupCommandLineInterface()

if __name__ == '__main__':
    main()