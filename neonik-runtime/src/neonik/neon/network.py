import math

class Network:

    delay: str | None
    incoming_bandwidth: str | None
    outgoing_bandwidth: str | None
    

    def __init__(self, delay=None, incoming_bandwidth=None, outgoing_bandwidth=None):
        """
            Set delay, outgoing and incoming bandwidth.

        Parameters
        ----------
        delay : str
            Delay for sending Packets. The time a packet is kept before actually sending it out. Specify as string, e.g. "5ms". Set to "None" to disable it.

        incoming_bandwidth : str
            The receiving or download speed. Specify as string, e.g. "1 gbit" or "5mbit". Set to "None" to disable it.
        
        outgoing_bandwidth : str
            The sending or upload speed. Specify as string, e.g. "1 gbit" or "5mbit". Set to "None" to disable it.
        """
        self.delay = delay
        self.incoming_bandwidth = incoming_bandwidth
        self.outgoing_bandwidth = outgoing_bandwidth

    def get_delay_in_seconds(self):
        if not self.delay:
            return 0
        elif self.delay.lower().endswith("ms"):
            return float(self.delay[:-2]) / 1000
        else:
            raise Exception("NOT IMPLEMENTED")

    @staticmethod
    def _convert_bandwidth_to_bits_per_seconds(bandwidth):
        if not bandwidth:
            return math.inf
        else:
            bandwidth = bandwidth.lower()
            if bandwidth.endswith("gbit"):
                return float(bandwidth[:-4]) * 10**9
            elif bandwidth.endswith("mbit"):
                return float(bandwidth[:-4]) * 10**6
            elif bandwidth.endswith("kbit"):
                return float(bandwidth[:-4]) * 10**3
            elif bandwidth.endswith("bit"):
                return float(bandwidth[:-3])
            else:
                raise Exception("NOT IMPLEMENTED")

    def get_incoming_bandwidth_in_bits_per_second(self):
        return Network._convert_bandwidth_to_bits_per_seconds(self.incoming_bandwidth)

    def get_outgoing_bandwidth_in_bits_per_second(self):
        return Network._convert_bandwidth_to_bits_per_seconds(self.outgoing_bandwidth)

    def __eq__(self, other):
        return type(other) == type(self) and \
               other.delay == self.delay and \
               other.incoming_bandwidth == self.incoming_bandwidth and \
               other.outgoing_bandwidth == self.outgoing_bandwidth

    def __hash__(self):
        return hash((self.delay, self.incoming_bandwidth, self.outgoing_bandwidth))

    def __str__(self):
        return f"delay {self.delay if self.delay else 'unrestricted'}, " \
               f"in {self.incoming_bandwidth if self.incoming_bandwidth else 'unrestricted'} " \
               f"out {self.outgoing_bandwidth if self.outgoing_bandwidth else 'unrestricted'}"
        
#: No network restrictions at all
Unlimited = Network(delay=None,
                    incoming_bandwidth=None,
                    outgoing_bandwidth=None)

#: LAN setting (check code for actual values)
LAN = Network(delay="1ms",
              incoming_bandwidth="1gbit",
              outgoing_bandwidth="1gbit")

#: WAN Enterprise setting (check code for actual values)
WAN_Enterprise = Network(delay="5ms",
                         incoming_bandwidth="1gbit",
                         outgoing_bandwidth="1gbit")

#: WAN (fast) (check code for actual values)
WAN_Fast = Network(delay="10ms",
                   incoming_bandwidth="1gbit",
                   outgoing_bandwidth="100mbit")

#: WAN (slow) (check code for actual values)
WAN_Slow = Network(delay="20ms",
                   incoming_bandwidth="100mbit",
                   outgoing_bandwidth="10mbit")

# The 5G performance metrics were chosen based on values from
# https://www.cnet.com/tech/mobile/5g-latency-why-speeding-up-networks-matters-faq/,
# https://commsbrief.com/is-5g-fast-average-5g-download-and-upload-speeds/,
# https://www.opensignal.com/2021/02/03/benchmarking-the-global-5g-experience,
# https://www.opensignal.com/reports/2022/01/usa/mobile-network-experience-5g,
# and https://www.opensignal.com/2022/03/17/benchmarking-the-5g-experience-asia-pacific-march-2022.

#: Average Mobile 5G Netwokr (check code for actual values)
Mobile_5G_Average = Network(delay="15ms",
                            incoming_bandwidth="150mbit",
                            outgoing_bandwidth="25mbit")

#: Slow Mobile 5G Netwokr (check code for actual values)
Mobile_5G_Slow = Network(delay="40ms",
                         incoming_bandwidth="50mbit",
                         outgoing_bandwidth="10mbit")
