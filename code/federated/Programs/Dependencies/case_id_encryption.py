from Compiler.types import sint, cint, Array
from Compiler.GC.types import sbits, sbitvec, sbit
from Compiler.library import *
from Compiler.circuit import Circuit

from abc import ABC, abstractmethod

from .utils.timers import CompilationTimeWatch


class CaseIdEncryption(ABC):
    @classmethod
    def encrypt_case_ids_with_random_key(cls, case_ids: Array, case_id_bitsize: int,
                                         debug_prints: bool = False) -> Array:
        # Building up on circuit from the docs:
        # https://mp-spdz.readthedocs.io/en/latest/Compiler.html?highlight=aes#module-Compiler.circuit
        from Compiler.instructions_base import set_global_vector_size, reset_global_vector_size

        aes128 = Circuit('aes_128')
        sb128 = sbits.get_type(128)

        n_events = len(case_ids)
        n_bits = case_id_bitsize + 1

        with CompilationTimeWatch("Key generation"):
            aes_key = cls._generate_aes_key(sb128)

        with CompilationTimeWatch("Conversion to sbitvec"):
            bits = case_ids[:].bit_decompose(n_bits)

            def mass_convert(intbits):
                n_elements = len(intbits)
                set_global_vector_size(n_elements)
                dabits = sint.get_dabit()
                reset_global_vector_size()
                return dabits[1] ^ intbits.bit_xor(dabits[0]).reveal()

            case_ids_as_bits = sbitvec.get_type(128).from_vec([mass_convert(bit) for bit in bits])

        with CompilationTimeWatch("AES circuit"):
            ciphertexts = aes128(sbitvec([aes_key] * n_events), case_ids_as_bits)
            # Reveal the ciphertext bits directly to cleartext and compose in cint, skipping
            # the sint.bit_compose round trip (which uses an edaBit + reveal to assemble the
            # bits in arithmetic form, only to be revealed again by the caller).
            revealed_bits = [b.reveal().to_regint_by_bit() for b in ciphertexts.bit_decompose(n_bits=case_id_bitsize)]
            return Array.create_from(cint.bit_compose(revealed_bits))


class CircuitAESCaseIdEncryption(CaseIdEncryption):
    @classmethod
    def _generate_aes_key(cls, sb128) -> "sbits":
        """Sample a 128-bit AES key.

        Default: arithmetic random bits converted to binary via dabits. The optimizer
        merges the 128 individual dabit reveals into a small number of online rounds,
        keeping communicated bits low.
        """
        return sb128.bit_compose([sbit(sint.get_random_bit()) for _ in range(128)])


class CircuitAESCaseIdEncryptionBinaryKey(CaseIdEncryption):
    """Variant of :class:`CircuitAESCaseIdEncryption` that samples the AES key directly
    in the binary domain (``sbits.get_random_bit``) instead of going through arithmetic
    random bits + dabit conversions.

    Trade-off compared to the default:
    - Far fewer online communication rounds in the case ID encryption step (the per-bit
      dabit reveals collapse to bit-protocol preprocessing).
    - Roughly 2x more bits sent over the wire (binary preprocessing tends to use more
      bandwidth than the merged arithmetic-domain reveals).

    Choose this variant when the network is round-limited (high latency); stick with the
    default when bandwidth is the bottleneck.
    """

    @classmethod
    def _generate_aes_key(cls, sb128) -> "sbits":
        return sb128.bit_compose([sbits.get_random_bit() for _ in range(128)])


class MiMCCaseIdEncryption(CaseIdEncryption):
    """MiMC PRF for case ID encryption, implemented directly in MP-SPDZ arithmetic.

    MiMC (Albrecht et al., Asiacrypt 2016) is a block cipher purpose-built for SMPC:
    every round is just a few field multiplications, with no bit-level XOR/AND. The
    round function is ``x -> (x + key + c_i)^d`` for some small odd d coprime with
    ``p - 1`` (so the power is a permutation in F_p).

    Trade-offs vs. the Bristol-AES variants:
    - No sint <-> sbits dabit pipeline: the whole encryption stays in the same prime
      field as the rest of the SMPC.
    - Per case ID: ~150-180 sequential field multiplications independent of input
      bit length, vs. ~252 binary AND rounds for AES at typical replicated 3-party
      protocols.
    - Operates natively in F_p, so it does not run on ring protocols and currently
      raises if ``program.prime`` is ``None``.
    - Newer than AES (2016 vs. 1998); for a PRF inside an honest-majority SMPC its
      security is well understood, but for adversarial use AES is the conservative
      choice.

    The degree ``d`` is chosen as the smallest of {3, 5, 7} such that
    ``gcd(d, p - 1) == 1``. For each ``d``, the round count is the standard
    128-bit-security parameter set (73 for d=3, 56 for d=5, 46 for d=7).
    """

    @classmethod
    def _choose_degree(cls) -> tuple[int, int]:
        """Return ``(d, num_rounds)`` such that ``x -> x^d`` is a permutation in
        ``F_p`` and ``num_rounds`` is the 128-bit-security count for that ``d``.

        If the prime is known at compile time, we verify gcd(d, p-1) = 1 and pick
        the smallest such ``d``. With ``-F`` / ``-R`` the prime/ring is decided
        only at runtime by the protocol binary, so we default to ``d = 3`` (works
        whenever p mod 3 != 1; safe for MP-SPDZ's standard -F 127 prime). If the
        actual prime makes cubing non-permutational, MiMC will produce collisions
        and the downstream DFG will be wrong — switch to d=5 or d=7 by subclassing.
        """
        from math import gcd
        p = get_program().prime
        if p is None:
            return 3, 73
        for d, num_rounds in [(3, 73), (5, 56), (7, 46)]:
            if gcd(d, p - 1) == 1:
                return d, num_rounds
        raise ValueError(
            f"none of d in (3, 5, 7) is a permutation exponent for p = {p}")

    @classmethod
    def _round_constants(cls, num_rounds: int) -> list[int]:
        """Public per-round constants, deterministically derived from a fixed seed.

        Truncated to 100 bits so each fits in any prime field with at least 100
        usable bits (well below ``p`` for ``-F 127``).
        """
        import hashlib
        mask = (1 << 100) - 1
        return [
            int.from_bytes(hashlib.sha256(f"MiMC-round-{i}".encode()).digest(), 'big') & mask
            for i in range(num_rounds)
        ]

    @classmethod
    def encrypt_case_ids_with_random_key(cls, case_ids: Array, case_id_bitsize: int,
                                         debug_prints: bool = False) -> Array:
        d, num_rounds = cls._choose_degree()
        round_constants = cls._round_constants(num_rounds)
        n_events = len(case_ids)

        with CompilationTimeWatch("Key generation"):
            # One uniformly random F_p element, broadcast to all events so the
            # per-event encryptions share the same key but run in parallel.
            key = sint.get_random().expand_to_vector(n_events)

        with CompilationTimeWatch(f"MiMC (d={d}, {num_rounds} rounds)"):
            state = case_ids.get_vector()
            for c in round_constants:
                state = state + key + c
                if d == 3:
                    state = state * state * state
                elif d == 5:
                    x2 = state * state
                    state = x2 * x2 * state
                else:  # d == 7
                    x2 = state * state
                    x4 = x2 * x2
                    state = x4 * x2 * state
            state = state + key  # final whitening, no power
            return Array.create_from(state.reveal())
