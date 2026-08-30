"""This file contains utilities regarding Shamir's secret sharing."""

import random
from typing import List

from .helper import mod_inverse
from tqdm import tqdm, trange


# We cache the lagrange coefficients so we don't have to re-compute them every time.
__LAGRANGE_CACHE = {}

def polynom(x, coefficients, prime):
    """Calculate the given polynomial at position x.

    Parameters
    ----------
    x : int
        Position where to evaluate the polynomial.
    coefficients : list
        Coefficients that define the polynomial.
    prime : int
        Modulo for the calculations.

    Returns
    -------
    int
        Value of the polynomial at postion x.
    """
    total = 0
    for i in range(len(coefficients)):
        # Adding here "% prime" makes it significantly slower, so do not add it!
        total += x**(len(coefficients)-i-1) * coefficients[i]

    return total % prime


def generate_coefficients(t, secret, prime):
    """Generate a random polynomial of degree t for a given secret.

    Parameters
    ----------
    t : int
        Threshold (number of parties required for Reconstructing), degree of the generated polynomial is t-1
    secret : int
        Secret to be shared.
    prime : int
        Prime for modulo.

    Returns
    -------
    list
        List of coefficients that define the polynomial uniquely.
    """
    coefficients = [random.randrange(0, prime) for _ in range(t-1)]
    coefficients.append(secret % prime)
    return coefficients

def generate_shares(n: int, t: int, secrets: list, prime: int) -> List[List[int]]:
    """Generate the shares for a given secret.

    Parameters
    ----------
    n : int
        Number of parties.
    t : int
        Threshold (number of parties required for Reconstructing), degree of the generated polynomial is t-1
    secrets : list
        List of secrets.
    prime : int
        Prime for modulo.

    Returns
    -------
    list
        List of lists of shares per party.
    """
    
    # Do not user [[]] * n, as then all inner [] reference to the same one
    shares = []
    for _ in range(n):
        shares.append([])
    
    for secret in tqdm(secrets, desc='Generating Shares', leave=None):
        coefficients = generate_coefficients(t, secret, prime)
        for i in range(n):
            shares[i].append(polynom(i+1, coefficients, prime))

    return shares



def generate_shares_insecurely(n: int, secrets: List[int]) -> List[List[int]]:
    """Generate shares insecurely, but fast

    Instead of creating actual polynoms to share a secret, we just set all shares to the secret itself.
    Therfore, for evaluation purpose, the sharing is still correct, but not "secure". However, this
    security implication has no effect on the runtime itself.

    Parameters
    ----------
    shares : list
        List of lists of shares of parties.
    prime : int
        Prime for modulo

    Returns
    -------
    list
        List of secrets.
    """
    return [secrets] * n


def reconstruct_lagrange(shares: List[List[int]], threshold: int, prime: int) -> List[int]:
    global __LAGRANGE_CACHE
    """Reconstruct the secret with lagrange interpolation.

    Parameters
    ----------
    shares : list
        List of lists of shares of parties.
    prime : int
        Prime for modulo

    Returns
    -------
    list
        List of secrets.
    """

    required_n_parties = threshold

    if required_n_parties == 0:
        return []

    if required_n_parties not in __LAGRANGE_CACHE.keys():
        coefficients = []
        for j in range(required_n_parties):
            x_j = j + 1
            product = 1
            for i in range(required_n_parties):
                x_i = i + 1
                if i != j:
                    product *= x_i * mod_inverse(x_i - x_j, prime)
            coefficients.append(product)
        __LAGRANGE_CACHE[required_n_parties] = coefficients

    secrets = []
    coefficients = __LAGRANGE_CACHE[required_n_parties]

    for z in trange(len(shares[0]), desc='Reconstructing Shares'):
        secret = 0
        for j in range(required_n_parties):
            secret += shares[j][z] * coefficients[j]
            secret %= prime
        secrets.append(secret)

    return secrets
