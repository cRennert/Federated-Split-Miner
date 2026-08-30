# Reviewer image for the federated Split Miner.
#
# Self-contained: public base images only, no credentials, no private registry.
# It carries the artifact, its Python environment and MP-SPDZ, so that a secure
# run needs nothing from the host but the container runtime.
#
# Build and run (rootless Podman keeps the capabilities below inside a user
# namespace, so nothing is granted on the host):
#
#     podman build -t federated-split-miner .
#     podman run --rm -it --init --cap-add=NET_ADMIN --cap-add=SYS_ADMIN \
#         federated-split-miner
#
# Under Docker on a Linux host, add `--security-opt apparmor=unconfined`: the
# default AppArmor profile denies `mount` whatever capabilities are held, and
# `ip netns add` bind-mounts under /run/netns to persist a namespace. The flag
# lifts the profile for this container only.
#
# `docker` works the same way, but its daemon runs as root, so prefer rootless
# Podman or Docker's rootless mode if the point is to avoid granting privilege.
#
# `--init` is not optional: pm4py reads the name of its parent process when it
# is imported, and as PID 1 there is no parent, so the import dies with
# `psutil.NoSuchProcess: process PID not found (pid=0)`. An init process takes
# PID 1 and everything below it has a parent again.
#
# Both capabilities are needed and neither is spare: NEONIK emulates the network
# with `tc ... netem`, which needs NET_ADMIN, and puts every party in its own
# network namespace, and `ip netns add` bind-mounts under /run/netns, which needs
# SYS_ADMIN. `--privileged` is not required.
ARG UV_VERSION=0.11.1
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# Debian rather than Alpine on purpose: the runtime pins zstd, and r4pm and
# rustxes publish manylinux wheels but no musllinux ones, so a glibc base
# installs from wheels instead of building them from source.
FROM python:3.14-slim AS base

# iproute2  the `ip` and `tc` the network emulation drives
# procps    `pgrep`, which the socket client uses to find the party processes
# iperf3    the bandwidth measurement between parties
# graphviz  the BPMN layout of the pooling step
# zstd      NEONIK compresses the computation reports with it once a run ends;
#           without it the run still succeeds but the reports are discarded,
#           and they are what the runtime tables are generated from
# libs      what the prebuilt MP-SPDZ party binaries link against
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils git zstd \
        iproute2 iputils-ping procps iperf3 graphviz \
        libsodium23 libssl3 libgmp10 libntl-dev libboost-thread-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

ENV NEONIK_MPSPDZ_PATH="/mp-spdz" \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /artifact

# The environment first, so that editing the artifact does not re-resolve it.
COPY pyproject.toml uv.lock ./
COPY neonik-runtime ./neonik-runtime
RUN uv sync --locked --no-install-project

COPY . /artifact

# MP-SPDZ at build time, so that a run starts immediately and every container
# carries the same version. Installed from the release tarball, which ships the
# party binaries prebuilt.
RUN uv run neonik-setup install-mpspdz --version 0.4.3 \
    && uv run python experiment-inputs/assemble_logs.py

ENV PATH="/artifact/.venv/bin:$PATH"

# NEONIK resolves the protocols as Programs/ under the working directory and
# imports main.py from it, so a run has to start here.
WORKDIR /artifact/code/federated

CMD ["/bin/bash"]
