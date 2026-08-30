import hashlib
import json
from typing import List, Tuple, Dict

import r4pm

Event = Tuple[int, int, int]


def use_activity_dict(activity_dict: Dict[str, int], activity: str) -> int:
    """
    Helper function to manage the activity dictionary that adds missing entries by giving them an index and returning
    the (added) index stored in the dictionary.
    :param activity_dict: A dictionary that maps activities to ongoing indices
    :param activity: An activity that is searched for in the dictionary
    :return: The index of the activity according to the dictionary
    """
    if activity not in activity_dict:
        activity_dict[activity] = len(activity_dict)
    return activity_dict[activity]


def hash_sha256_with_bit_size(value: str, num_of_bits: int, hash_dict: Dict[tuple[str, int], int]) -> int:
    """
    Hashes a value to a given bit representation using SHA-256
    :param value: An value to hash
    :param num_of_bits: The number of bits for the hash
    :return: A hash of the value with the num_of_bits bits
    """
    if (value, num_of_bits) not in hash_dict:
        hash_dict[value, num_of_bits] = (int.from_bytes(hashlib.sha256(value.encode('utf-8')).digest()[:8], 'little') &
                            ((1 << num_of_bits) - 1))

    return hash_dict[value, num_of_bits]


def read_event_logs(
        event_log_path_prefix: str,
        number_of_parties: int,
        timestamp_bitsize: int,
        case_id_hash_size: int,
        combined_sort: bool,
) -> Tuple[List[List[Event]], int, List[str]]:
    """
    Reads in the provided event log files in the given path and encodes the data from all files in a single list
    containing tuples of ((padded) case_id + timestamp, activity, case_id).
    :param event_log_path_prefix: The prefix of the event log files
    :param number_of_parties: The number of parties collaborating / providing input data
    :param timestamp_bitsize: The bitsize of the timestamps to be considered.
    :param case_id_hash_size: The bitsize of the case ID hash
    :param combined_sort: A boolean that indicates whether to sort the cases according combined key of timestamp and case ID
    :return: A single list containing all events; represented as tuples containing a combined value of case ID and
    timestamp, the activity, and the case ID of the event

    Example:
    >>> encoded_event_logs, num_of_activities = read_event_logs('data/directory_name/file_name_prefix', 3, 63, 63)
    Expects files in the format for all parties as input with the number of parties in the file_name, i.e., 3 files:
    - file_name_prefix_3_0
    - file_name_prefix_3_1
    - file_name_prefix_3_2
    to be present in the relative path data/directory_name/
    """
    result = list()

    activity_dict = dict()

    hash_dict = dict()

    for i in range(0, number_of_parties):
        sub_result = list()
        event_log, _ = r4pm.df.import_xes(f'{event_log_path_prefix}_{number_of_parties}_{i}.xes.gz')
        event_log = list(event_log[['case:concept:name', 'concept:name', 'time:timestamp']].iter_rows())

        for case_id, activity, timestamp in event_log:
            case_id = hash_sha256_with_bit_size(case_id, case_id_hash_size, hash_dict)
            activity = use_activity_dict(activity_dict, activity)
            timestamp = int(timestamp.timestamp())

            if combined_sort:
                sub_result.append((case_id, activity, (case_id << timestamp_bitsize) + timestamp))
            else:
                sub_result.append((case_id, activity, timestamp))
        result.append(sub_result)

    sorted_activities = [act for act, _ in sorted(activity_dict.items(), key=lambda key_val: key_val[1])]

    return result, len(activity_dict), sorted_activities


def bin_event_logs(party_inputs: List[List[Event]], hash_length: int, num_of_deciding_bits: int) \
        -> List[List[List[Event]]]:
    """
    Assigning bins for all parties' inputs based on the leading bits of the hashed case IDs
    :param party_inputs: The preprocessed inputs of each party
    :param hash_length: The length of the hash of the case ID
    :param num_of_deciding_bits: The number of bits to bin based on the case ID
    :return: A list of bins that contain subsets of the data based on the case IDs leading bits
    """
    num_of_parties = len(party_inputs)
    num_bins = 1 << num_of_deciding_bits
    shift = hash_length - num_of_deciding_bits

    bins_result: List[List[List[Event]]] = [[[] for _ in range(num_of_parties)] for _ in range(num_bins)]

    for party_index, entry in enumerate(party_inputs):
        for record in entry:
            bins_result[record[0] >> shift][party_index].append(record)

    return bins_result
