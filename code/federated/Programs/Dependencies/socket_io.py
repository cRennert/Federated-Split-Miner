"""Reusable MP-SPDZ-side socket helpers.

Encapsulates the listen / accept / close lifecycle and the per-type read/write
primitives so that a program using sockets only has to deal with computation,
not the boilerplate around connection management.

Typical use::

    with MPSPDZSocketSession(port=14000) as session:
        session.read_regint()                  # consume any handshake flag
        session.send_cints([revealed_vector])  # one batched send
        received = session.read_cints(n=5)     # list of 5 cint scalars
"""

from Compiler.library import (
    accept_client_connection,
    closeclientconnection,
    cint,
    listen_for_clients,
    print_ln,
    regint,
)


class MPSPDZSocketSession:
    """Context-managed session with a single external client.

    ``__enter__`` opens the listener on ``port`` and blocks until a client
    connects. ``__exit__`` closes the per-client connection. The instance
    exposes the per-type read/write helpers below.
    """

    def __init__(self, port: int = 14000):
        self.port = port
        self.client_id = None

    def __enter__(self):
        listen_for_clients(self.port)
        print_ln("Listening for client connections on base port %s", self.port)
        self.client_id = accept_client_connection(self.port)
        print_ln("Client connected (client_socket_id=%s)", self.client_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.client_id is not None:
            closeclientconnection(self.client_id)
            self.client_id = None
        return False

    # ---- input ----------------------------------------------------------

    def read_regint(self):
        """Read a single regint from the client (typically a handshake flag)."""
        return regint.read_from_socket(self.client_id)

    def read_cints(self, n: int = 1):
        """Read ``n`` clear field elements. Returns a single cint if ``n == 1``,
        otherwise a list of cint scalars."""
        return cint.read_from_socket(self.client_id, n=n)

    # ---- output ---------------------------------------------------------

    def send_cints(self, values):
        """Send a list of cint values. Each element may itself be a vector
        (all elements must share the same ``size``); the parties broadcast the
        same payload to the client."""
        cint.write_to_socket(self.client_id, list(values))

    # ---- strings --------------------------------------------------------
    # Wire format: ``cint(length)`` followed by ``cint(byte)`` for each UTF-8
    # byte of the string. Length-prefixing means the receiver can recover a
    # variable-length string while only the maximum length needs to be agreed
    # on at compile time (the SMPC side has fixed-size reads).

    def send_string(self, s: str) -> None:
        """Send a UTF-8 string to the client. The client receives the actual
        length from the octetStream framing — no padding needed in this
        direction since the wire size matches ``len(s.encode())``.

        Pairs with :meth:`SocketClientSession.receive_string` on the client.
        """
        data = s.encode("utf-8")
        payload = [cint(len(data))] + [cint(b) for b in data]
        cint.write_to_socket(self.client_id, payload)

    def read_string(self, max_length: int):
        """Read a string of at most ``max_length`` UTF-8 bytes from the client.

        Returns ``(length, byte_cints)`` where:
        - ``length`` is a ``cint`` holding the actual byte length sent.
        - ``byte_cints`` is a length-``max_length`` list of ``cint`` scalars
          (zero-padded beyond the actual length).

        ``max_length`` MUST equal the value the client passes to
        :meth:`SocketClientSession.send_string`.
        """
        values = cint.read_from_socket(self.client_id, n=max_length + 1)
        return values[0], values[1:]