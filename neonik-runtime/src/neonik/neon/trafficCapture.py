"""
This class allows to "safely" capture traffic, where safe means it is more likely that all traffic is captured.
Note, the super safe option is only available if you do not send randomly pings

How to use:

.. code-block:: python

    # Create SafeTcpdump object (Use arguments as required)
    pcap_storage_path = "/home/...."
    pcap_base_name = "capture.pcap"
    safe_tcpdump = SafeTcpdump(['-n', '-B', f'{1024**1}', '-i', 'your_interface_name', '-w', join_path_abs(pcap_storage_path, pcap_base_name)])
    # Start Safe Traffic Capture with an IP to be pinged -> can be checked later
    safe_tcpdump.start_capture_with_ping("172.16.1.11", pcap_storage_path)


    # Do what you want to do and capture traffic in background


    # Finish capture and check
    try:
        # Stop the capture
        safe_tcpdump.stop_capture()

        # Get the total traffic and a list with all icmp messages
        total_traffic_with_icmp, list_of_all_icmp_messages = digest_pcaps(pcap_storage_path, pcap_base_name, exclude_last=False, check_for_icmp_messages=True)
        
        # Check if all icmp messages are unique, and if the expected number if occuring
        # If you capture all traffic, it should be four messages
        # If you capture only outgoing or only incoming traffic it should be only 2 messages
        icmp_compensation = check_and_return_icmp_message_length(list_of_all_icmp_messages, 4)
        
        # Substract the size of the icmp messages from the total_traffic_with_icmp to get actual traffic size
        total_traffic = total_traffic_with_icmp - icmp_compensation

    except SafeTcpdumpException as e:
        logger.critical("Capture probelm: {e}")
        exit(1)


Note, NEON creates the network interfaces only as necessary. This means, that in order to have any network interface or the bridge, you first need to run any SMPC procol at least once. This will create the necessary network interfaces, and they are keept until NEON is exited. This needs to be also done when the number of parties increases. However, for the creation of the network interfaces it is enough to run an "empty" SMPC protocol (e.g., ``xenon_hack.mpc``) that doesn't compute anything. So you can do it roughly as follows: 1) set the number of parties, 2) execute an "empty" SMPC protocol, 3) start the capture, 4) execute the actual SMPC protocol, 5) stop the capture. You can repeat steps 3-5 as desired, as long as the number of parties does not change.
"""

import os

from .helper import join_path_abs, get_logger, get_path_to_mpspdz, PopenWithExit
import subprocess
import time

import logging
logger = get_logger("Capture")
logger.level = logging.WARNING


class SafeTcpdumpException(Exception):
    """Simple Excpetion for Safe tcpdump"""
    pass

def get_icmp_messages_from_pcap(pcap_file_path):
    """
    Extract the timestamp and length of any icmp messages present

    Returns a list of all icmp messages found
    """
    logger.debug(f"Starting Tshark: {pcap_file_path}")
    tshark_proc = subprocess.Popen(['tshark', '-r', pcap_file_path, '-Y', 'icmp', '-o', 'gui.column.format:"Len","%L","Time","%Yut"'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = tshark_proc.communicate()
    list_of_all_icmp_messages = []
    for ol in output.decode('utf-8').strip().split('\n'):
        if ol:
            ol_split = ol.split(' ', maxsplit=1)
            icmp_len = int(ol_split[0])
            icmp_time = ol_split[1]
            list_of_all_icmp_messages.append((icmp_time,icmp_len))
    return list_of_all_icmp_messages


def check_and_return_icmp_message_length(list_of_icmps, num_icmps_expected):
    """
    Check if all ICMP messages (exactly num_icmps_expected) are present, each with different time stamp
    """
    if len(set([k for k,v in list_of_icmps])) != len(list_of_icmps):
        raise SafeTcpdumpException(f"Found at least one ICMP message twice")
    elif len(list_of_icmps) != num_icmps_expected:
        raise SafeTcpdumpException(f"Number of ICMP message missmatch: Expected {num_icmps_expected} Found {len(list_of_icmps)} - {list_of_icmps}")
    else:
        # Return the sum of all individual icmp messages
        return sum([v for k,v in list_of_icmps])


def get_total_traffic_in_pcap(pcap_file_path):
    """
    Return the total length of all packets sent or received (including any ICMP messages)
    """
    capinfos_proc = PopenWithExit(['capinfos', '-d', '-M', '-T', '-r', pcap_file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, error = capinfos_proc.communicate()
    # Note, separator is "tab" "\t"
    return int(output.decode('utf-8').split('\t')[1])


def do_ping(ip_to_ping):
    """
    just a simple ping
    """
    ping_proc = PopenWithExit(f"ping -c 1 {ip_to_ping}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ping_proc.communicate()


def digest_pcaps(pcap_storage_path, pcap_base_name, exclude_last, check_for_icmp_messages):
    """
    Digest Pcaps of the form <pcap_base_name>, <pcap_base_name>1, <pcap_base_name>2, ...
    Exclude last is usefull for ongoing capture (with option -C <split_size>), so to not delete the the file currently written to.
    """
    # Get all pcaps and order them by name/suffix
    list_with_number = []
    len_pcap_base_name = len(pcap_base_name)
    # Todo maybe faster to just search for the max
    for file_name in os.listdir(pcap_storage_path):
        # Only select files that fit the base name
        if file_name.startswith(pcap_base_name):
            # Extract their number
            # Note, tcpdump names the file name.pcap, name.pcap1, name.pcap2, ...
            tmp_number_str = file_name[len_pcap_base_name:]
            tmp_number = int(tmp_number_str) if tmp_number_str else 0
            list_with_number.append((tmp_number, file_name))
    # Sort
    list_with_number.sort(key=lambda x: x[0])

    if exclude_last:
        # If last should be excluded, remove it from the list
        list_with_number = list_with_number[:-1]

    # Remove number and make it the full path
    list_of_pcaps = [join_path_abs(pcap_storage_path, file_name) for _, file_name in list_with_number]

    total_traffic = 0
    list_of_all_icmp_messages = []
    for full_path_to_file in list_of_pcaps:
        if check_for_icmp_messages:
            list_of_all_icmp_messages.extend(get_icmp_messages_from_pcap(full_path_to_file))
        total_traffic += get_total_traffic_in_pcap(full_path_to_file)
        os.remove(full_path_to_file)
    return total_traffic, list_of_all_icmp_messages


class SafeTcpdump:
    """
    Safely capture network traffic
    """


    def __init__(self, tcpdump_argument_list):
        """
        TCP dump options
        # -n no name lookup
        # -B buffersize in KiB (needs to be large enough to capture all packets)
        # -C split capture files by max size in MB
        # -f capture filter, select only ICMP or PUSH packets: icmp || (tcp[tcpflags] & tcp-push != 0) and restrict to possible IP adresses
        # -i interface
        # -w output-file
        """
        self.tcpdump_argument_list = tcpdump_argument_list




    def start_capture_with_ping(self, ip_to_ping_for_verification, pcap_file_path):
        """
        Start the capture

        If ip_to_ping_for_verification is not None, then a ping will be included
        """
        self.__ip_to_ping_for_verification = ip_to_ping_for_verification
        self.__tcp_proc = PopenWithExit(['tcpdump'] + self.tcpdump_argument_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Ensure capture has started successfully
        while not os.path.exists(pcap_file_path):
            logger.debug(f"Waiting for capture file being created.")
            time.sleep(0.1)
        logger.info("Traffic capture started")

        
        if self.__ip_to_ping_for_verification:
            # ADD an ICMP messages, that can be later detected in the traffic
            # This is used to ensure we are actually capturing traffic
            do_ping(self.__ip_to_ping_for_verification)




    def stop_capture(self, time_to_wait_for_finish=2):
        # we send again a ping for verification that we have actually the complete traffic
        if self.__ip_to_ping_for_verification:
            do_ping(self.__ip_to_ping_for_verification)
        # And wait so that it should be captured (required, enlarge if necessary)
        logger.info("Waiting for possible final traffic.")
        time.sleep(time_to_wait_for_finish)

        # Before actually terminating the capture, make a final check that tcpdump is still running
        if self.__tcp_proc.poll() != None:
            # Process was killed early -> Bad
            raise SafeTcpdumpException(f"TCPDUMP was killed to early. Capture will be incomplete.")

        # Now, terminate it.
        self.__tcp_proc.terminate()
        output, error = self.__tcp_proc.communicate()

        # Tcpdump output looks like:
        # 28220 packets captured
        # 28221 packets received by filter
        # 0 packets dropped by kernel
        # (one empty line)
        # -> So we you use a simple/hacky way to check for errors
        if error.decode('utf-8').split("\n")[-2] != "0 packets dropped by kernel":
            raise SafeTcpdumpException(f"Packets dropped, you might try increasing the buffer size.\n" + 
                                        f"TCPDUMP OUTPUT:\n{output.decode('utf-8')}\n" +
                                        f"TCPDUMP ERROR:\n{error.decode('utf-8')}")
