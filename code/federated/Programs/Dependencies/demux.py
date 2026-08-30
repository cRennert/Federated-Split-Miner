import math
import os.path
from abc import ABC, abstractmethod

from Compiler.GC.types import *
from Compiler.GC.types import Array, sint
from Compiler.comparison import PreMulC
from Compiler.library import *
from Compiler.library import Array, Matrix, sint, crash
from Compiler.types import *
from Compiler.types import Array, Matrix, sint
from sympy import symbols, Poly, fraction


class DemuxProtocol(ABC):

    @classmethod
    @abstractmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        """ Demultiplex the secret index `x` into an Array of length `n` that only contains zeroes except a single one at position `x`.
        If the condition `cond` is provided, the resulting Array will be returned only if `cond` is one, and an all-zero Array will be returned if `cond` is zero.

        :param x: the secret index of the resulting unitvector
        :param n: the size of the resulting unitvector
        :param cond: optional condition, the unitvector is only returned if cond = 1. If cond = 0, an all-zero Array is returned.
        :returns An `sint` Array of length `n`."""
        pass

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        """ Demultiplexes multiple secret indeces `x` into a batch of Arrays of length `n` that only contains zeroes except a single one at position `x`.

        :param x_es: the secret indices of the resulting unitvectors
        :param n: the size of the resulting unitvectors
        :param conds: optional conditions, if conds[i] = 0, then result[i] will only contain zeroes, and the unitvector if conds[i] = 1
        :returns An `sint` Matrix, where the i'th row contains the unitvector corresponding to `x_es[i]`."""
        result = sint.Matrix(rows=len(x_es), columns=n)
        for i in range(len(x_es)):
            if conds is not None:
                result[i][:] = cls.demux(x_es[i], n, conds[i])[:]
            else:
                result[i][:] = cls.demux(x_es[i], n)[:]
        return result

    @classmethod
    def demux_many(cls, x_es: list[sint], ns: list[int], conds: list[sint | None] | None = None) -> list[Array]:
        """ Perform many demultiplexes of varying sizes in parallel. This was introduced for demultiplexes whose simple `demux` method is not mergeable.
        May raise NotImplementedErrors.

        :param x_es: the secret indices of the resulting unitvectors
        :param ns: the sizes of the resulting unitvectors
        :param conds: optional conditions, if conds[i] = 0, then result[i] will only contain zeroes, and the unitvector if conds[i] = 1
        :returns A list of the resulting unit_vectors."""
        result = []
        for i in range(len(x_es)):
            if conds is not None:
                result.append(cls.demux(x_es[i], ns[i], conds[i])[:])
            else:
                result.append(cls.demux(x_es[i], ns[i])[:])
        return result


class DemuxKeller(DemuxProtocol):
    """
    Performs a demultiplex bit by bit, resulting in a `O(log n)` round protocol. This variation applies the bit-packing technique introduced by Keller et al. [1], which reduces the
    number of arithmetric-field multiplication but requires final bit-decompositions. Ehrmanntraut and Meyer find that this generally is slower than the version without bitpacking [2].

    [1]: Keller et al., "Faster Secure Multi-party Computation of AES and DES Using Lookup Tables", https://doi.org/10.1007/978-3-319-61204-1_12
    [2]: Ehrmanntraut and Meyer, "Going faster: Privacy-Preserving Shortest Paths from Start to End"
    """

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        l = math.ceil(math.log2(n))
        s = x.bit_decompose(l)
        max_bits_per_word = 64

        p = sint.Array(1)
        p[0] = (2 * (1 - s[0]) + s[0])
        if cond is not None:
            p[0] *= cond

        total_bits = 2
        bits_per_word = 2
        for j in range(1, l):
            if total_bits < max_bits_per_word:
                t = p[0] * s[j]
                p[0] = (2 ** total_bits) * (p[0] - t) + t
                bits_per_word *= 2
            else:
                t = p[:] * s[j]
                new_p = sint.Array(2 * len(p))
                new_p.assign_vector(t, base=0)
                new_p.assign_vector(p[:] - t, base=len(p))
                p = new_p
            total_bits *= 2

        # print_ln("bits=%s", [x.reveal() for x in p.bit_decompose(2**l)])
        # bits = p.bit_decompose(2**l)
        result = sint.Array(n)
        for i, x in enumerate(p):
            x_bits = list(reversed(x.bit_decompose(bits_per_word)))
            curr_size = min(n - i * bits_per_word, bits_per_word)
            # It may be that we have more words than strictly needed, becuase each iteration doubles the number
            # of words. For example, when n=192, we actually generate 4 words (as if n=256), and ignore the last word.
            # This is not the most beautiful way to ignore unneeded words, but we do not have to worry about edge-cases
            # that way.
            if curr_size <= 0:
                break
            for j, bit in enumerate(x_bits[:curr_size]):
                result[i * bits_per_word + j] = sint(bit)
        return result

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        l = math.ceil(math.log2(n))
        all_bits = [x.bit_decompose(l) for x in x_es]
        p = [Array.create_from([(2 * (1 - s[0]) + s[0]) for s in all_bits])[:]]
        if conds is not None:
            p[0] *= conds[:]

        total_bits = 2
        bits_per_word = 2
        max_bits_per_word = 64

        for j in range(1, l):
            mask = Array.create_from([all_bits[i][j] for i in range(len(x_es))])[:]
            if total_bits < max_bits_per_word:
                t = p[0] * mask
                p[0] = (2 ** total_bits) * (p[0] - t) + t
                bits_per_word *= 2
            else:
                t = [p_row * mask for p_row in p]
                new_p = [p_row - t_row for p_row, t_row in zip(p, t)]
                new_p.extend(t)
                p = new_p
            total_bits *= 2

        result = sint.Matrix(rows=len(x_es), columns=n)
        for i, x in enumerate(p):
            x_bits = list(reversed(x.bit_decompose(bits_per_word)))
            curr_size = min(n - i * bits_per_word, bits_per_word)
            # It may be that we have more words than strictly needed, becuase each iteration doubles the number
            # of words. For example, when n=192, we actually generate 4 words (as if n=256), and ignore the last word.
            # This is not the most beautiful way to ignore unneeded words, but we do not have to worry about edge-cases
            # that way.
            if curr_size <= 0:
                break
            for j, bits in enumerate(x_bits[:curr_size]):
                result.set_column(i * bits_per_word + j, bits)
        return result


class DemuxKellerWithoutBitpacking(DemuxProtocol):
    """
    Performs a demultiplex bit by bit, resulting in a `O(log n)` round protocol.
    Keller et al. [1] originally propose "packing" the bits into larger words, requiring fewer multiplications but bit-decompositions at the end.
    [2] find that, in practice, it is often faster not to pack bits, which is the variation implemented in this class.

    [1]: Keller et al., "Faster Secure Multi-party Computation of AES and DES Using Lookup Tables", https://doi.org/10.1007/978-3-319-61204-1_12
    [2]: Ehrmanntraut and Meyer, "Going faster: Privacy-Preserving Shortest Paths from Start to End"
    """

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        p = sint.Array(1)
        if cond is not None:
            p[0] = cond
        else:
            p[0] = 1

        # start_timer(101)
        l = math.ceil(math.log2(n))
        bits = x.bit_decompose(l)
        # stop_timer(101)
        for bit in bits:
            # start_timer(102)
            t = sint(bit) * p[:]
            # stop_timer(102)
            # start_timer(103)
            new_p = sint.Array(2 * len(p))
            new_p.assign_vector(p[:] - t, base=0)
            # stop_timer(103)
            # start_timer(104)
            new_p.assign_vector(t, base=len(p))
            # stop_timer(104)
            p = new_p

        return p.get_part(0, n)

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        # This uses a list of vectors instead of a matrix to avoid store / load instructions.
        # This sadly is necessary to have reasonable compile times.

        # The compiler's instruction merger corrupts this protocol when multiple
        # demux_batch calls share a basic block (observed: all-zero output for the
        # bottom_demux call in build_directly_follows_graph_and_its_artifacts once
        # the start/stop_timer barriers around it were removed). Isolate each call
        # in its own basic block until the underlying compiler issue is resolved.
        break_point()

        # Base approach: Same a non-batched, but the demultiplexed arrays are stored in the columns of the p "matrix".
        p = []
        if conds is not None:
            p.append(conds[:])
        else:
            p.append(cint(1, size=len(x_es)))

        l = math.ceil(math.log2(n))

        all_bits_vecs = x_es[:].bit_decompose(l)

        for i in range(l):
            mask = all_bits_vecs[i]
            t = [p_row * mask for p_row in p]
            new_p = [p_row - t_row for p_row, t_row in zip(p, t)]
            new_p.extend(t)
            p = new_p

        result = sint.Matrix(len(x_es), n)
        for i in range(n):
            result.set_column(i, p[i])

        # See barrier comment at the top of this method.
        break_point()
        return result

class DemuxLaunchburry(DemuxProtocol):

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        return Array.create_from(
            DemuxLaunchburry._demux0(list(reversed(x.bit_decompose(math.ceil(math.log2(n))))), cond)).get_part(0, n)

    @staticmethod
    def _demux0(bits: list[sint], cond: sint | None = None) -> sint:
        if len(bits) == 1:
            result = Array.create_from([1 - bits[0], bits[0]])[:]
            if cond is not None:
                result *= cond
            return result

        mid = len(bits) // 2
        first_half = DemuxLaunchburry._demux0(bits[:mid], cond)
        second_half = DemuxLaunchburry._demux0(bits[mid:], cond)

        result = sint(size=len(first_half) * len(second_half))
        args = [result]
        for b in first_half:
            args.append(len(second_half))
            args.append(b * second_half)
        concats(*args)
        return result


class DemuxByComparison(DemuxProtocol):
    """Performs a "trivial" demultiplex using many parallel comparisons. Surprisingly fast for small sizes."""

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        if cond is not None:
            x += n * (1 - cond)

        # At least when using replicated sharing based protocols with split-comparisons, >= is faster than ==.
        # Therefore, we perform <= and determine the difference to the following entry ("for free"), to perform an equals operation.
        n_bits = math.ceil(math.log2(n))
        clear_indices = regint.inc(n)
        comp_result = x.greater_equal(clear_indices, bit_length=n_bits)  # (clear_indices <= x)

        result = Array.create_from(comp_result)
        result.assign_vector(result.get_vector(
            base=0, size=n - 1) - result.get_vector(base=1, size=n - 1))

        return result

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        n_demuxes = len(x_es)
        if conds is not None:
            x_es[:] += n * (1 - conds[:])

        clear_indices = regint.inc(n_demuxes * n, wrap=n)
        # Matrix.create_from(tmp_matrix).print_reveal_nested()
        tmp_matrix2 = sint.Matrix(rows=n_demuxes, columns=n)

        @for_range(n_demuxes)
        def build_tmp_matrices_outer(i: cint) -> None:
            tmp_matrix2[i].assign_all(x_es[i])

        n_bits = math.ceil(math.log2(n))
        tmp_matrix2[:] = tmp_matrix2[:].greater_equal(clear_indices,
                                                      bit_length=n_bits)  # (clear_indices <= tmp_matrix2[:])

        @for_range(n_demuxes)
        def diff_outer(i: cint) -> None:
            tmp_matrix2[i].assign_vector(tmp_matrix2[i].get_vector(
                base=0, size=n - 1) - tmp_matrix2[i].get_vector(base=1, size=n - 1))
            if conds is not None:
                tmp_matrix2[i][n - 1] -= 1 - conds[i]

        return tmp_matrix2


class DemuxByPolynomials(DemuxProtocol):
    """
    A demultiplex protocol that expresses the demux function as n polynomials of n'th degree and evaluates them using Catrina's and de Hoogh's constant round multiplication trick.
    This code was first published in the artifact of [1], but not discussed in the paper as this method is fairly slow in practice and only works in fields, not rings.

    [1]: Ehrmanntraut and Meyer, "Going faster: Privacy-Preserving Shortest Paths from Start to End"
    """
    lagrange_basis_matrix_cache = {}

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        p = get_program().prime
        print("Prime: ", p)
        if p is None:
            print("Polynomial-based demultiplex requires a known prime modulus.")
            assert p is not None

        if cond is not None:
            x = cond.if_else(x, n)

        basis = cls._build_lagrange_basis(n, p)

        tmp = PreMulC([x + 1 for _ in range(n + 1)])
        powers_of_x = sint.Array(n + 1)
        powers_of_x[0] = 1
        for i in range(n):
            powers_of_x[i + 1] = tmp[i]

        return basis.dot(powers_of_x.to_column_matrix()).to_array()

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        p = get_program().prime
        print("Prime: ", p)
        if p is None:
            print("Polynomial-based demultiplex requires a known prime modulus.")
            assert p is not None

        x_es[:] += 1
        if conds is not None:
            x_es[:] = conds[:].if_else(x_es[:], n)
        basis = cls._build_lagrange_basis(n, p)

        # Own reimplementation of constant round premul (column_wise)
        randoms = sint.Matrix(n, n)
        inverses = sint.Matrix(n, n)

        randoms[:], inverses[:] = sint.get_random_inverse(size=n ** 2)

        for i in range(n - 1):
            randoms[i + 1][:] *= inverses[i][:]
        # break_point()
        for i in range(n):
            randoms[i][:] *= x_es[:]

        clear_values = randoms.reveal()
        # clear_values.print_reveal_nested()
        for i in range(n - 1):
            clear_values[i + 1][:] *= clear_values[i][:]

        inverses[:] *= clear_values[:]

        powers_of_x = sint.Matrix(n + 1, n)
        powers_of_x[0].assign_all(1)
        powers_of_x.assign_part_vector(inverses[:], base=1)

        # powers_of_x.print_reveal_nested()

        return (basis.dot(powers_of_x)).transpose()

    @classmethod
    def _build_lagrange_basis(cls, n: int, p: int) -> Matrix:
        # There are two caches:
        # The LAGRANGE_BASIS_MATRIX_CACHE ensures that the basis-matrix has to be loaded into MP-SPDZ memory only once per protocol execution,
        # whereas the file cache ensures that the basis is computed only once in forever.
        if n in cls.lagrange_basis_matrix_cache:
            return cls.lagrange_basis_matrix_cache[n]

        cache_folder = "lagrange-cache"
        os.makedirs(cache_folder, exist_ok=True)
        cache_filename = os.path.join("lagrange-cache", f"v1-{p}-{n}.txt")

        if os.path.isfile(cache_filename):
            result = cint.Matrix(n, n + 1)
            with open(cache_filename, 'r') as f:
                data = f.readline()
                data = data.split(",")
                for i in range(n):
                    for j in range(n + 1):
                        result[i][j] = int(data[i * (n + 1) + j])

            cls.lagrange_basis_matrix_cache[n] = result
            return result

        basis = cint.Matrix(n, n + 1)
        cache_data = []

        lagrangeX = symbols('x')
        for i in range(n):
            x_i = i + 1
            lagrange_polynomial = 1
            for j in range(n + 1):
                x_j = j + 1
                if x_i == x_j:
                    continue
                lagrange_polynomial *= (lagrangeX - x_j) / (x_i - x_j)
            coefficients = Poly(lagrange_polynomial).coeffs()

            for j, c in enumerate(reversed(coefficients)):
                nominator, denominator = fraction(c)
                val = (int(nominator) * pow(int(denominator), -1, p)) % p
                basis[i][j] = val
                cache_data.append(val)
            print(f"Built basis {i + 1}/{n}...")

        with open(cache_filename, "w") as f:
            f.write(",".join([str(coeff) for coeff in cache_data]) + "\n")

        cls.lagrange_basis_matrix_cache[n] = basis
        return basis


class DemuxByShuffleReveal(DemuxProtocol):

    @classmethod
    def demux(cls, x: sint, n: int, cond: sint | None = None) -> Array:
        bitsize = math.ceil(math.log2(n))
        preprocessing_size = 2 ** bitsize

        random_unitvector = sint.Array(preprocessing_size)
        random_unitvector.assign_all(0)
        if cond is not None:
            random_unitvector[0] = cond
        else:
            random_unitvector[0] = 1
        random_unitvector.secure_shuffle()

        random_unitvector_index = (regint.inc(preprocessing_size) * random_unitvector[:]).sum()

        if cond is not None:
            random_unitvector_index += (1 - cond) * \
                sint.get_random_int(bits=bitsize)

        if get_program().options.ring is not None:
            # Inspired by comparison.Mod2mRing
            rem_bits = int(get_program().options.ring) - bitsize
            shift = ((x - random_unitvector_index + preprocessing_size) << rem_bits).reveal() >> rem_bits
        else:
            # This was determined to use much fewer rounds and only slightly more communication than the "Add mask * preprocessing_size, reveal, modulo"
            # approach for public-result modulo.
            shift = (x - random_unitvector_index + preprocessing_size) % preprocessing_size
            shift = shift.reveal()
        result = sint.Array(n)

        @for_range(n)
        def _(i: cint) -> None:
            pos = (i - shift + preprocessing_size) % preprocessing_size
            result[i] = random_unitvector[pos]

        return result

    @classmethod
    def demux_batch(cls, x_es: Array, n: int, conds: Array | None = None) -> Matrix:
        bitsize = math.ceil(math.log2(n))
        preprocessing_size = 2 ** bitsize

        random_unit_vectors = sint.Matrix(len(x_es), preprocessing_size)
        random_unit_vectors.assign_all(0)

        @for_range(len(x_es))
        def init_unit_vectors(i: cint) -> None:
            if conds is not None:
                random_unit_vectors[i][0] = conds[i]
            else:
                random_unit_vectors[i][0] = 1

        for i in range(len(x_es)):
            random_unit_vectors[i].secure_permute(sint.get_secure_shuffle(preprocessing_size))
        unitvectors_indices = sint.Array(len(x_es))
        unitvectors_indices.assign_all(0)

        @for_range(preprocessing_size)
        def calculate_indices(i: cint) -> None:
            unitvectors_indices[:] += i * random_unit_vectors.get_column(i)

        if conds is not None:
            masks = sint.Array(len(x_es))
            for i in range(len(masks)):
                masks[i] = sint.get_random_int(bits=bitsize)
            unitvectors_indices[:] += (1 - conds[:]) * masks[:]

        shifts = cint.Array(len(x_es))
        if get_program().options.ring is not None:
            # Inspired by comparison.Mod2mRing
            rem_bits = int(get_program().options.ring) - bitsize
            shifts[:] = ((x_es[:] - unitvectors_indices[:] + preprocessing_size) << rem_bits).reveal() >> rem_bits
        else:
            shifts[:] = ((x_es[:] - unitvectors_indices[:] + preprocessing_size) % preprocessing_size).reveal()

        result = sint.Matrix(len(x_es), n)

        @for_range(len(x_es))
        def build_result(i: cint) -> None:
            @for_range(n)
            def rotate_unitvector(j: cint) -> None:
                pos = (j - shifts[i] + preprocessing_size) % preprocessing_size
                result[i][j] = random_unit_vectors[i][pos]

        return result
