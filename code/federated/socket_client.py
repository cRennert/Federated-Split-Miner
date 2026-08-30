"""Reusable Python-side client for talking to MP-SPDZ via the ``MPSPDZSocketSession``
helper on the SMPC side.

Encapsulates:
- Discovery of the per-run WorkDir created by neonik (where the party + client
  TLS certs live).
- Auto-re-exec inside the party-0 network namespace when running on this
  container's bridge network (the parties are not reachable from the host's
  default netns).
- The MP-SPDZ ``Client`` connection and a few typed read/write helpers.
- Provisioning of the client TLS cert into the active run's WorkDir (neonik
  only puts the party certs there), so the parties accept the handshake.

Typical use::

    from socket_client import SocketClientSession

    with SocketClientSession() as session:
        session.send_handshake_flag()
        values = session.receive_cint_values()
        session.send_cint_values([v * 10 + 1 for v in values])

No manual cert setup is required: :func:`ensure_client_cert` generates a
``C0`` certificate into the run's ``Player-Data`` and rehashes it on every
connection (see its docstring for why this has to happen per run).
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
from typing import Iterable, List, Optional

# Anchor default paths to this file's directory (the project root) rather than a
# hardcoded absolute path, so the client keeps working if the project is renamed
# or checked out elsewhere.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_BRIDGE_SUBNET_PREFIX = "172.16.1"
_NETNS_SENTINEL_ENV = "_SOCKET_CLIENT_IN_NETNS"


def _resolve_mpspdz_path() -> str:
    """Return the MP-SPDZ install root as an absolute path, honoring
    ``NEONIK_MPSPDZ_PATH``.

    In the production container the docker image installs MP-SPDZ at ``/mp-spdz``
    and points ``NEONIK_MPSPDZ_PATH`` at it, whereas a dev checkout keeps it in
    ``<project>/mp-spdz`` (and may set the env var to a *relative* ``mp-spdz``).
    Preferring the env var (which is exactly what neonik itself uses, see
    ``NeonConfig.from_environment_vars``) keeps both working. The result must be
    absolute because :meth:`SocketClientSession.__enter__` ``os.chdir``s into the
    run's WorkDir before importing ``client`` from ``ExternalIO`` — a relative
    path would otherwise resolve against the WorkDir and fail to import."""
    env = os.environ.get("NEONIK_MPSPDZ_PATH")
    raw = env if env else os.path.join(_PROJECT_ROOT, "mp-spdz")
    # Absolutized against the cwd at import time (process launch dir), which is
    # before any chdir happens.
    return os.path.abspath(raw)


def _default_temp_glob() -> str:
    """Return the glob that matches neonik's per-run ``WorkDir_*/Player-Data``.

    neonik creates its per-run WorkDirs under ``<workdir>/temp``, where the
    workdir defaults to the *installed neonik package's* parent (i.e.
    ``site-packages/temp``) unless ``NEONIK_INSTALL_DIR`` overrides it. Ask
    neonik where that is instead of assuming the venv lives next to this file:
    in the production container the package is under ``/app/.venv`` while this
    file may be copied elsewhere, so a ``_PROJECT_ROOT/.venv/...`` guess would
    miss. Falls back to the historical layout if neonik can't be imported."""
    try:
        from neonik.neon.helper import get_path_to_temp
        temp = get_path_to_temp(os.environ.get("NEONIK_INSTALL_DIR") or None)
        return os.path.join(temp, "WorkDir_*", "Player-Data")
    except Exception:
        return os.path.join(
            _PROJECT_ROOT,
            ".venv/lib/python3.*/site-packages/temp/WorkDir_*/Player-Data",
        )


_DEFAULT_TEMP_GLOB = _default_temp_glob()
_DEFAULT_MPSPDZ_EXTERNAL_IO = os.path.join(_resolve_mpspdz_path(), "ExternalIO")


def find_active_workdir(
    glob_pattern: str = _DEFAULT_TEMP_GLOB,
    required_files: Iterable[str] = ("P0.pem", "P1.pem", "P2.pem"),
    retries: int = 30,
    delay: float = 1.0,
) -> str:
    """Return the most-recently-modified WorkDir/Player-Data that has all the
    required cert files. Retries while a fresh SMPC run is spinning up.

    Only the *party* certs are required: neonik creates a fresh WorkDir per run
    that contains those but no client cert. Matching on the client cert here
    would bind us to a stale WorkDir from a previous run whose party certs no
    longer match the live parties (TLS then fails with ``unknown ca``). The
    client cert is provisioned into the selected WorkDir by
    :func:`ensure_client_cert`."""
    required = list(required_files)
    for _ in range(retries):
        candidates = [
            p for p in glob.glob(glob_pattern)
            if all(os.path.exists(os.path.join(p, f)) for f in required)
        ]
        if candidates:
            return max(candidates, key=os.path.getmtime)
        time.sleep(delay)
    raise RuntimeError(
        f"no WorkDir with {required} found under {glob_pattern!r}. "
        "Make sure the SMPC computation is running.")


def ensure_client_cert(player_data_dir: str, client_id: int = 0) -> None:
    """Make sure a client certificate (``C<id>.pem`` / ``C<id>.key``) lives in
    ``player_data_dir`` and is discoverable by the parties' capath verification.

    neonik creates a fresh WorkDir per run containing only the party certs, so
    the client cert has to be (re)generated into that same ``Player-Data`` and
    the directory rehashed. Otherwise the parties reject the TLS handshake with
    ``unknown ca``: their context does ``add_verify_path(Player-Data)`` (a
    lazy, hash-based capath lookup), so they only trust the client cert once a
    ``<subject_hash>.N`` symlink for it exists in that directory.

    The cert's subject is ``/CN=C<id>`` because the MP-SPDZ party verifies the
    client against the host name ``C<id>`` (see ``Processor/ExternalClients.cpp``).
    """
    name = f"C{client_id}"
    pem = os.path.join(player_data_dir, f"{name}.pem")
    key = os.path.join(player_data_dir, f"{name}.key")
    if not (os.path.exists(pem) and os.path.exists(key)):
        subprocess.run(
            ["openssl", "req", "-newkey", "rsa", "-nodes", "-x509",
             "-out", pem, "-keyout", key, "-subj", f"/CN={name}"],
            check=True, capture_output=True,
        )
    _ensure_capath_symlink(player_data_dir, pem)


def _ensure_capath_symlink(player_data_dir: str, cert_path: str) -> None:
    """Create the ``<subject_hash>.N`` symlink OpenSSL's capath lookup needs to
    find ``cert_path`` (the single-cert equivalent of ``c_rehash``)."""
    subject_hash = subprocess.check_output(
        ["openssl", "x509", "-subject_hash", "-noout", "-in", cert_path],
        text=True,
    ).strip()
    target = os.path.basename(cert_path)
    n = 0
    while True:
        link = os.path.join(player_data_dir, f"{subject_hash}.{n}")
        if os.path.islink(link) and os.readlink(link) == target:
            return
        if os.path.lexists(link):
            n += 1
            continue
        os.symlink(target, link)
        return


def find_party_netns(party_id: int = 0) -> Optional[str]:
    """Return the name of the network namespace hosting an MP-SPDZ party
    process, or ``None`` if no such process or netns can be found.

    Matches any MP-SPDZ party binary rather than one protocol: the evaluation
    runs ``replicated-ring-party.x`` and ``sy-rep-ring-party.x``, so naming a
    single executable here left the client in the host namespace, from which the
    parties are unreachable, and the run hung with the computation waiting for a
    client that never connected."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", rf"-party\.x.*--player {party_id}"],
            text=True)
    except subprocess.CalledProcessError:
        return None
    pids = out.strip().split("\n")
    if not pids or not pids[0]:
        return None
    try:
        target = os.readlink(f"/proc/{pids[-1]}/ns/net")
    except OSError:
        return None
    try:
        candidates = os.listdir("/var/run/netns")
    except OSError:
        return None
    for entry in candidates:
        path = f"/var/run/netns/{entry}"
        try:
            if f"net:[{os.stat(path).st_ino}]" == target:
                return entry
        except OSError:
            continue
    return None


def reexec_into_party_netns_if_needed(party_id: int = 0) -> None:
    """If we're not already inside the party's netns and the parties live in
    one, ``execvpe`` into ``ip netns exec <ns> python <our args>``. Sets a
    sentinel env var so the re-exec'd copy doesn't loop.

    Returns silently if no re-exec is needed (e.g. the parties are reachable
    from the current netns, or no party process is running yet)."""
    if _NETNS_SENTINEL_ENV in os.environ:
        return
    ns = find_party_netns(party_id)
    if ns is None:
        return
    print(f"Re-executing inside network namespace {ns}", flush=True)
    os.execvpe(
        "ip",
        ["ip", "netns", "exec", ns, sys.executable, *sys.argv],
        {**os.environ, _NETNS_SENTINEL_ENV: "1"},
    )


class SocketClientSession:
    """Context-managed connection to all MP-SPDZ parties.

    Handles WorkDir discovery, optional netns re-exec, TLS setup, and exposes
    typed read/write helpers. Designed to pair with
    ``Programs/Dependencies/socket_io.py:MPSPDZSocketSession`` on the SMPC side.
    """

    def __init__(
        self,
        n_parties: int = 3,
        port_base: int = 14000,
        my_client_id: int = 0,
        host_prefix: str = _DEFAULT_BRIDGE_SUBNET_PREFIX,
        host_offset: int = 11,
        external_io_path: str = _DEFAULT_MPSPDZ_EXTERNAL_IO,
        auto_netns: bool = True,
    ):
        self.n_parties = n_parties
        self.port_base = port_base
        self.my_client_id = my_client_id
        self.hosts = [f"{host_prefix}.{host_offset + i}" for i in range(n_parties)]
        self.external_io_path = external_io_path
        self.auto_netns = auto_netns
        self._client = None
        self._octet_stream_cls = None

    def __enter__(self) -> "SocketClientSession":
        if self.auto_netns:
            reexec_into_party_netns_if_needed(party_id=0)

        player_data = find_active_workdir()
        ensure_client_cert(player_data, self.my_client_id)
        work_dir = os.path.dirname(player_data)
        os.chdir(work_dir)
        if self.external_io_path not in sys.path:
            sys.path.insert(0, self.external_io_path)

        # Imported lazily so that the netns re-exec happens first.
        from client import Client, octetStream  # type: ignore

        self._octet_stream_cls = octetStream
        self._client = Client(self.hosts, self.port_base, self.my_client_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._client is not None:
            for sock in self._client.sockets:
                try:
                    sock.close()
                except Exception:
                    pass
            self._client = None
        return False

    # ---- output ---------------------------------------------------------

    def send_handshake_flag(self, flag: int = 1) -> None:
        """Send a single regint (matches an `MPSPDZSocketSession.read_regint()`
        call on the SMPC side)."""
        assert self._client is not None and self._octet_stream_cls is not None
        for sock in self._client.sockets:
            os_stream = self._octet_stream_cls()
            os_stream.store(flag)
            os_stream.Send(sock)

    def send_cint_values(self, values: Iterable[int]) -> None:
        """Send a list of public cleartext values; each party receives the same
        bytes (matches an `MPSPDZSocketSession.read_cints(n=len(values))` call
        on the SMPC side)."""
        assert self._client is not None
        # ``send_public_inputs`` packs with the *share* domain, and unlike the
        # receiving direction (see :meth:`_receive_plain_values`) that is what
        # the VM wants here: ``cint.read_from_socket`` reads share-domain-sized
        # elements. Verified against sy-rep-ring, where the two domains differ
        # (13 vs 8 bytes) -- packing 8-byte elements makes the party abort with
        # "READSOCKETC: insufficient data".
        self._client.send_public_inputs(list(values))

    # ---- input ----------------------------------------------------------

    def _receive_plain_values(self, sock) -> List[int]:
        """Read one batched ``cint.write_to_socket`` payload from a party.

        Replaces ``Client.receive_plain_values``, which unpacks the payload
        with ``Client.domain`` -- the *share* domain -- instead of
        ``Client.clear_domain``. The two coincide for every protocol whose
        shares live in the same domain as its clear values (replicated-ring,
        replicated-field, ...), which is why the upstream helper works there.
        They differ for the SPDZ-wise protocols: ``sy-rep-ring`` shares a
        value together with its MAC over Z_2^(k+s), so the connection
        specification announces domain = Z_2^104 (13 bytes) while the parties
        keep writing clear values as Z_2^64 (8 bytes). Upstream then trips
        ``assert len(os) % self.domain.size() == 0`` -- e.g. a 5x5 s-FPDFG is
        200 bytes on the wire and 200 % 13 != 0 -- killing the client, which
        the parties only see as a reset connection while they wait for it.
        """
        assert self._client is not None and self._octet_stream_cls is not None
        domain = getattr(self._client, "clear_domain", None) or self._client.domain
        stream = self._octet_stream_cls()
        stream.Receive(sock)
        size = domain.size()
        if len(stream) % size:
            raise ValueError(
                f"payload of {len(stream)} bytes is not a multiple of the "
                f"clear domain size {size}")
        return [int(stream.get(domain)) for _ in range(len(stream) // size)]

    def receive_cint_values(self, expect_agreement: bool = True) -> List[int]:
        """Receive one batched send from each party. By default checks that all
        parties sent identical payloads (matches an
        `MPSPDZSocketSession.send_cints([vec])` call on the SMPC side)."""
        assert self._client is not None
        per_party = [
            self._receive_plain_values(sock)
            for sock in self._client.sockets
        ]
        if expect_agreement:
            for v in per_party[1:]:
                assert v == per_party[0], (
                    f"inconsistent values across parties: {per_party}")
        return per_party[0]

    # ---- strings --------------------------------------------------------
    # Wire format: cint(length) followed by cint(byte) for each UTF-8 byte.
    # Length-prefixing lets the SMPC side recover the actual byte count even
    # though it has to allocate a fixed-size buffer up front.

    def send_string(self, s: str, max_length: int) -> None:
        """Send a UTF-8 string padded with zero bytes up to ``max_length``.

        The MP-SPDZ side reads ``max_length + 1`` cint values via
        :meth:`MPSPDZSocketSession.read_string(max_length)`, where the leading
        cint is the real byte length and the remaining ``max_length`` cints
        are the (zero-padded) bytes.

        Raises ``ValueError`` if ``s`` encodes to more than ``max_length``
        UTF-8 bytes.
        """
        data = s.encode("utf-8")
        if len(data) > max_length:
            raise ValueError(
                f"string too long: {len(data)} bytes > max_length {max_length}")
        padded = data + b"\x00" * (max_length - len(data))
        payload = [len(data)] + list(padded)
        self.send_cint_values(payload)

    def receive_string(self) -> str:
        """Receive a UTF-8 string sent by :meth:`MPSPDZSocketSession.send_string`.

        The SMPC side sends ``length`` plus the actual bytes (no padding in
        this direction), so we read whatever the parties emitted and slice.
        """
        values = self.receive_cint_values()
        length = values[0]
        return bytes(values[1:1 + length]).decode("utf-8")
