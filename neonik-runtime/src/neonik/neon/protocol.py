"""
This class contains some pre-defined protocols.
"""

import importlib
import inspect
from enum import Enum, auto
import math
from .helper import NeonException


class Domain(Enum):
    PRIME = auto()
    BINARY = auto()
    RING = auto()  # mod 2^k


class Protocol():
    """
    Base class for defining an MP-SPDZ protocol
    """
    executable: str
    domain: Domain
    min_number_of_parties: int
    max_number_of_parties: int
    supports_secret: bool
    # This is None if threshold is not supported, otherwise the factor.
    threshold_factor: int | None
    supports_bits_from_squares: bool
    supports_batch_size: bool

    def __init__(self, executable: str,
                 domain: Domain,
                 min_number_of_parties,
                 max_number_of_parties,
                 supports_secret,
                 threshold_factor=None,
                 supports_bits_from_squares=True,
                 supports_batch_size=True):
        """
            Create a new Protocol supported by MP-SPDZ

        Parameters
        ----------
        executable : str
            Name of the executable
        domain : Domain
            Choose a domain of the protocol (prime, binary, ring).
        min_number_of_parties: int
            Lowest number of parties supported (typically 2 or 3)
        max_number_of_parties: int
            Highest number of parties supported (can be also math.inf if there is no theoretical limit. The value is tyically 2, 3 or math.inf)
        supports_secret: bool
            Whether NEON has support for distributing shared values which can be read by MP-SPDZ instruction `read_from_file`. They are essentially the same as if `write_to_file` is executed in MP-SPDZ.
        threshold_factor: int
            Default None. If the protocol uses a threshold (e.g. like Shamir), the threshold factor determines the actual threshold values as math.ceil(number_of_parties / threshold_factor).
            IMPORTANT: This is only used for secret reconstruction! Not for setting the actual threshold of the protocol.
        supports_bits_from_squares: bool
            Whether the protocol supports the option bits-from-squares. Default is True, as most support it.
        supports_batch_size: bool
            Whether the protocol supports the option batchsize. Default is True, as most support it.
        """
        self.executable = executable
        self.domain = domain
        self.min_number_of_parties = min_number_of_parties
        self.max_number_of_parties = max_number_of_parties
        self.supports_secret = supports_secret
        self.threshold_factor = threshold_factor
        self.supports_bits_from_squares = supports_bits_from_squares
        self.supports_batch_size = supports_batch_size

    def __str__(self):
        temp = self.executable.rpartition('-party.x')[0]
        if temp:
            return temp
        return self.executable.rpartition('.x')[0]

    # Computes the default threshold if possible
    def get_threshold(self, number_of_parties):
        if self.threshold_factor:
            return math.ceil(number_of_parties / self.threshold_factor)
        else:
            raise NeonException(f"Threshold not implemented for '{self}'.")

    @staticmethod
    def from_name(name: str) -> "Protocol | None":
        protocol = importlib.import_module("neonik.neon.protocol")
        for obj_name, obj in inspect.getmembers(protocol):
            if not isinstance(obj, Protocol):
                continue

            if name.lower() == obj_name.lower():
                return obj
        return None



#:
Atlas = Protocol(executable="atlas-party.x",
                 domain=Domain.PRIME,
                 min_number_of_parties=3,
                 max_number_of_parties=math.inf,
                 supports_secret=True,
                 threshold_factor=2)

# Currently not working
#:
# BMRProgram = Protocol(executable="bmr-program-party.x",
#                       domain=Domain.RING,
#                       min_number_of_parties=3,
#                       max_number_of_parties=3,
#                       supports_secret=False)

#:
Brain = Protocol(executable="brain-party.x",
                 domain=Domain.RING,
                 min_number_of_parties=3,
                 max_number_of_parties=3,
                 supports_secret=False)

#:
CCD = Protocol(executable="ccd-party.x",
               domain=Domain.BINARY,
               min_number_of_parties=3,
               max_number_of_parties=math.inf,
               supports_secret=False,
               threshold_factor=2)

#:
ChaiGear = Protocol(executable="chaigear-party.x",
                    domain=Domain.PRIME,
                    min_number_of_parties=2,
                    max_number_of_parties=math.inf,
                    supports_secret=False)

#:
CowGear = Protocol(executable="cowgear-party.x",
                   domain=Domain.PRIME,
                   min_number_of_parties=2,
                   max_number_of_parties=math.inf,
                   supports_secret=False)

#:
DealerRing = Protocol(executable="dealer-ring-party.x",
                      domain=Domain.RING,
                      min_number_of_parties=3,
                      max_number_of_parties=math.inf,
                      supports_secret=False)

# Currently not working
#:
# Emulate = Protocol(executable="emulate.x",
#                    domain=Domain.RING,
#                    min_number_of_parties=1,
#                    max_number_of_parties=1,
#                    supports_secret=False)

#:
Hemi = Protocol(executable="hemi-party.x",
                domain=Domain.PRIME,
                min_number_of_parties=2,
                max_number_of_parties=math.inf,
                supports_secret=False)

#:
HighGear = Protocol(executable="highgear-party.x",
                    domain=Domain.PRIME,
                    min_number_of_parties=2,
                    max_number_of_parties=math.inf,
                    supports_secret=False)

#:
LowGear = Protocol(executable="lowgear-party.x",
                   domain=Domain.PRIME,
                   min_number_of_parties=2,
                   max_number_of_parties=math.inf,
                   supports_secret=False)

#:
MaliciousCCD = Protocol(executable="malicious-ccd-party.x",
                        domain=Domain.BINARY,
                        min_number_of_parties=3,
                        max_number_of_parties=math.inf,
                        supports_secret=False,
                        threshold_factor=2)

#:
MaliciousRepBin = Protocol(executable="malicious-rep-bin-party.x",
                           domain=Domain.BINARY,
                           min_number_of_parties=3,
                           max_number_of_parties=3,
                           supports_secret=False)

#:
MaliciousRepField = Protocol(executable="malicious-rep-field-party.x",
                             domain=Domain.PRIME,
                             min_number_of_parties=3,
                             max_number_of_parties=3,
                             supports_secret=False)

#:
MaliciousRepRing = Protocol(executable="malicious-rep-ring-party.x",
                           domain=Domain.RING,
                           min_number_of_parties=3,
                           max_number_of_parties=3,
                           supports_secret=False)

# Note Malicious shamir assumes here actually that just below half of the parties are corrupted. In the standard setting with 1/3 of the parties corrupted, threshold_factor would have to be 3
#:
MaliciousShamir = Protocol(executable="malicious-shamir-party.x",
                           domain=Domain.PRIME,
                           min_number_of_parties=3,
                           max_number_of_parties=math.inf,
                           supports_secret=True,
                           threshold_factor=2)

#:
MalRepBMR = Protocol(executable="mal-rep-bmr-party.x",
                     domain=Domain.BINARY,
                     min_number_of_parties=3,
                     max_number_of_parties=3,
                     supports_secret=False)

#:
MalShamirBMR = Protocol(executable="mal-shamir-bmr-party.x",
                        domain=Domain.BINARY,
                        min_number_of_parties=3,
                        max_number_of_parties=3,
                        supports_secret=False)

# Currently not working
#:
# MAMA = Protocol(executable="mama-party.x",
#                 domain=Domain.PRIME,
#                 min_number_of_parties=2,
#                 max_number_of_parties=math.inv,
#                 supports_secret=False)

Mascot = Protocol(executable="mascot-party.x",
                  domain=Domain.PRIME,
                  min_number_of_parties=2,
                  max_number_of_parties=math.inf,
                  supports_secret=False)

# Currently not working
#:
# NO = Protocol(executable="no-party.x",
#               domain=Domain.RING,
#               min_number_of_parties=2,
#               max_number_of_parties=math.inf,
#               supports_secret=False)

#:
PSRepBin = Protocol(executable="ps-rep-bin-party.x",
                    domain=Domain.BINARY,
                    min_number_of_parties=3,
                    max_number_of_parties=3,
                    supports_secret=False)

#:
PSRepField = Protocol(executable="ps-rep-field-party.x",
                      domain=Domain.PRIME,
                      min_number_of_parties=3,
                      max_number_of_parties=3,
                      supports_secret=False)

#:
PSRepRing = Protocol(executable="ps-rep-ring-party.x",
                     domain=Domain.RING,
                     min_number_of_parties=3,
                     max_number_of_parties=3,
                     supports_secret=False)

#:
RealBMR = Protocol(executable="real-bmr-party.x",
                   domain=Domain.BINARY,
                   min_number_of_parties=2,
                   max_number_of_parties=3,
                   supports_secret=False)

#:
Rep4Ring = Protocol(executable="rep4-ring-party.x",
                    domain=Domain.RING,
                    min_number_of_parties=4,
                    max_number_of_parties=4,
                    supports_secret=False)

#:
RepBMR = Protocol(executable="rep-bmr-party.x",
                  domain=Domain.BINARY,
                  min_number_of_parties=3,
                  max_number_of_parties=3,
                  supports_secret=False)

#:
ReplicatedBin = Protocol(executable="replicated-bin-party.x",
                         domain=Domain.BINARY,
                         min_number_of_parties=3,
                         max_number_of_parties=3,
                         supports_secret=False)

#:
ReplicatedField = Protocol(executable="replicated-field-party.x",
                           domain=Domain.PRIME,
                           min_number_of_parties=3,
                           max_number_of_parties=3,
                           supports_secret=False)

#:
ReplicatedRing = Protocol(executable="replicated-ring-party.x",
                          domain=Domain.RING,
                          min_number_of_parties=3,
                          max_number_of_parties=3,
                          supports_secret=False)

#:
Semi2K = Protocol(executable="semi2k-party.x",
                  domain=Domain.RING,
                  min_number_of_parties=2,
                  max_number_of_parties=math.inf,
                  supports_secret=False)

#:
SemiBin = Protocol(executable="semi-bin-party.x",
                   domain=Domain.BINARY,
                   min_number_of_parties=2,
                   max_number_of_parties=math.inf,
                   supports_secret=False)

#:
SemiBMR = Protocol(executable="semi-bmr-party.x",
                   domain=Domain.BINARY,
                   min_number_of_parties=2,
                   max_number_of_parties=3,
                   supports_secret=False)

#:
Semi = Protocol(executable="semi-party.x",
                domain=Domain.PRIME,
                min_number_of_parties=2,
                max_number_of_parties=math.inf,
                supports_secret=False)

#:
Shamir = Protocol(executable="shamir-party.x",
                  domain=Domain.PRIME,
                  min_number_of_parties=3,
                  max_number_of_parties=math.inf,
                  supports_secret=True,
                  threshold_factor=2)

#:
ShamirBMR = Protocol(executable="shamir-bmr-party.x",
                     domain=Domain.BINARY,
                     min_number_of_parties=3,
                     max_number_of_parties=3,
                     supports_secret=False)

#:
Soho = Protocol(executable="soho-party.x",
                domain=Domain.PRIME,
                min_number_of_parties=2,
                max_number_of_parties=math.inf,
                supports_secret=False)

#:
SPDZ2K = Protocol(executable="spdz2k-party.x",
                  domain=Domain.RING,
                  min_number_of_parties=2,
                  max_number_of_parties=math.inf,
                  supports_secret=False)

#:
SYRepField = Protocol(executable="sy-rep-field-party.x",
                      domain=Domain.PRIME,
                      min_number_of_parties=3,
                      max_number_of_parties=3,
                      supports_secret=False)

#:
SYRepRing = Protocol(executable="sy-rep-ring-party.x",
                     domain=Domain.RING,
                     min_number_of_parties=3,
                     max_number_of_parties=3,
                     supports_secret=False)

#:
SYShamir = Protocol(executable="sy-shamir-party.x",
                    domain=Domain.PRIME,
                    min_number_of_parties=3,
                    max_number_of_parties=math.inf,
                    supports_secret=False,
                    threshold_factor=2)

#:
Temi = Protocol(executable="temi-party.x",
                domain=Domain.PRIME,
                min_number_of_parties=2,
                max_number_of_parties=math.inf,
                supports_secret=False)

#:
Tinier = Protocol(executable="tinier-party.x",
                  domain=Domain.BINARY,
                  min_number_of_parties=2,
                  max_number_of_parties=math.inf,
                  supports_secret=False)

# Requires compilation with -DINSECURE
#:
# Tiny = Protocol(executable="tiny-party.x",
#                 domain=Domain.BINARY,
#                 min_number_of_parties=2,
#                 max_number_of_parties=math.inf,
#                 supports_secret=False)

#:
Yao = Protocol(executable="yao-party.x",
               domain=Domain.BINARY,
               min_number_of_parties=2,
               max_number_of_parties=2,
               supports_secret=False,
               supports_bits_from_squares=False,
               supports_batch_size=False)
