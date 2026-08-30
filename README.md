# A Federated Split Miner: Secure Discovery of Cross-Organizational BPMN Models

This repository is the artifact for the paper *"A Federated Split Miner: Secure
Discovery of Cross-Organizational BPMN Models"*. It contains

* the implementation (in [`code`](code)), which is split into the cleartext
  reference implementation of our variant ([`code/reference`](code/reference))
  and the MP-SPDZ implementation of every protocol presented in the paper
  ([`code/federated`](code/federated)),
* our version of NEON in [`neonik-runtime`](neonik-runtime), used to emulate the
  network settings and to capture the reported metrics,
* the partial event logs that the federated runs read
  (in [`experiment-inputs`](experiment-inputs)),
* every process model the evaluation discovered
  (in [`experiment-outputs`](experiment-outputs)), and
* the conformance measurements behind the quality table, together with the
  generated tables themselves (in [`experiment-results`](experiment-results)).

## Repository layout

```
pyproject.toml, uv.lock      the environment, for the repository as a whole
code/
  reference/                   the cleartext reference implementation
    reference_split_miner.py     our Split Miner variant over a plain event log
    generate_reference_models.py discovers every model of the evaluation
  federated/                   the secure implementation
    Programs/                    MP-SPDZ protocols
      split_miner.mpc              entry point of a secure run
      Dependencies/split_miner.py  the protocols of Section 5
      Dependencies/dfg.py          directly-follows graph construction
      Dependencies/demux.py        the demultiplex building block
    split_miner_socket.py        turns revealed artifacts into a BPMN collaboration
    main.py                      driver of a single computation
    generate_experiments.py      writes the NEONIK experiment specifications
    extract_model_outputs.py     pulls the models out of the computation reports
    plot-eval.py                 turns computation reports into figures and tables
    neonik-project.yml           declares the experiment parameters
neonik-runtime/              our version of NEON
experiment-inputs/
  split_logs.py                the seeded splitter that produces the partial logs
  split_sepsis_by_department.py splits the Sepsis log along the three departments
  assemble_logs.py             joins the BPIC 18 log back from its two parts
  original-logs/               the ten published event logs, unmodified
  partial-logs/<log>/          the three partial logs per event log, plus a manifest
experiment-outputs/
  models/<log>/                the discovered models, one folder per event log
experiment-results/
  conformance/                 fitness and precision, one file per setting
    raw/                         the measurement batches as delivered
  tables/                      the tables of the paper, as LaTeX and Markdown
  evaluation-reports/          raw runtime reports (see below)
```

## Requirements

* **To rediscover the process models** using the reference implementation (non-federated mirror): 
  * [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
* **To re-run the secure evaluation**, which the reported runtimes rest on:
  * [Docker](https://docs.docker.com/get-started/)

* [MP-SPDZ](https://mp-spdz.readthedocs.io/en/latest/) (needs no build toolchain as the prebuilt release is already downloaded)

## Installation

For creating a local virtual environment and recreating the BPIC 18:
```bash
uv sync
uv run python experiment-inputs/assemble_logs.py
```

For running the SMPC Split Miner, we setup Neonik and MP-SPDZ

```bash
export NEONIK_MPSPDZ_PATH="$PWD/mp-spdz"
cd code/federated
uv run neonik-setup install-mpspdz --version 0.4.3
```

Then setup further directories and paths:
```bash
mkdir -p code/federated/temp/MP
ln -s "$PWD/mp-spdz" code/federated/temp/MP/mp-spdz-0.4.3
```

## Using the reference implementation as an alternative to the MP-SPDZ installation

We created some reference implementation of the federated code that is also capable to produce
the models using regular Python code.
It mirrors the computation of the protocols and can thus be used to rediscover the output models
of the experiments and to also apply the suggested adapted Split Miner implementation to other
event logs.
However, the models can also be generated using MP-SPDZ and the full federated pipeline if the used
machine provides enough RAM and the user the corresponding hardware / time for the computation.

```bash
uv run python code/reference/generate_reference_models.py
```

Passing one or more folder
names of [`experiment-outputs/models`](experiment-outputs/models) restricts it to
those logs.

The script discovers each model twice, once from the original log and once from
the three partial logs of the federated split, and compares both against the
model that the original Split Miner 1.0 produces on the same log. It reports,
per event log, the number of nodes and sequence flows and the arcs on which the
three models differ. Running it on the partial logs alone needs nothing beyond
this repository, and so do the runs on the original logs, which are bundled in
[`experiment-inputs/original-logs`](experiment-inputs/original-logs). The fitness and precision computations
were done with ProM and therefore not a protocol provided here.

Both thresholds are fixed to the values used throughout the paper, $\epsilon =
0.1$ and $\eta = 0.4$, which are the defaults of the original tool and carry
its meaning: a larger $\eta$ filters harder.

## Running your own evaluation

Setting up the docker for the federated Split Miner variant.
The following commands needs to be run from the root of this directory.

If needed:
```bash
cd ../..
```

Then:
```bash
docker build -t federated-split-miner .
docker run --rm -it --init --cap-add=NET_ADMIN --cap-add=SYS_ADMIN \
    --security-opt apparmor=unconfined federated-split-miner
```

You are then asked to choose an option from 1, 2, and 3, which only impacts the key of your stored results.
You can choose 2 or 3.
Then the evaluation of the runtime of the protocol is applied, and they are written to 
In the terminal, one can see the intermediate runtimes and which step is currently applied.

The image carries the artifact, its environment and MP-SPDZ 0.4.3, and starts in
`code/federated`, so inside it the evaluation is

Optional (if not in the subdirectory already):
```bash
cd code/federated
```

Then:
```bash
uv run python generate_experiments.py
run-all-experiments experiment-specs/split-miner.yml
```

Both paths are relative to the current directory, so the specifications are
written to `code/federated/experiment-specs` and read back from there.

The specifications cover both SMPC primitives of the paper, the semi-honest
`replicated-ring` and the maliciously secure `sy-rep-ring`, over three
organizations. A full run over all ten event logs takes days rather than hours,
and BPIC 11, BPIC 18, and BPIC 19 are excluded from the malicious setting.

## Protocol names

The protocols carry the names of the paper and are defined in
[`code/federated/Programs/Dependencies/split_miner.py`](code/federated/Programs/Dependencies/split_miner.py)
unless stated otherwise.

| In the paper | In the code |
|---|---|
| $\Pi_\text{EventEncoding}$ | `dfg.py: build_directly_follows_graph_and_its_artifacts_fully_obliviously` |
| $\Pi_\text{DFG}$ | `dfg.py: build_left_right_directly_follows_graph` |
| $\Pi_\text{SelfLoops}$ | `detect_self_loops` |
| $\Pi_\text{ShortLoops}$ | `detect_short_loops` |
| $\Pi_\text{Concurrency}$ | `detect_concurrency` |
| $\Pi_\text{Prune}$ | `dfg_pruning` |
| $\Pi_\text{MaxCapacity}$ | `find_best_incoming_edges_dijkstra`, `find_best_outgoing_edges` |
| $\Pi_\text{EtaFilter}$ | `keep_eta_percentile_edges` |
| $\Pi_\text{Sanitize}$ | `sanitize_conc_matrix` |
| $\Pi_\text{Binning}$ | `activity_to_organization_binning` |
| $\Pi_\text{SplitMiner}$ | `build_split_miner_artifacts`, `discover_bpmn` |
| $\Pi_\text{Pooling}$ | `split_miner_socket.py` |

## What is not in this repository

* **The raw computation reports** of the secure runs, as described above.

Everything else is here, the original event logs included, so that the
evaluation can be rerun without fetching anything. The event logs are 217 MB of
the 226 MB this repository takes, and none of them needs Git LFS: the one file
that would have, the 151 MB BPIC 18 log, is committed in two parts that
`experiment-inputs/assemble_logs.py` joins back.

## Licenses

This repository contains [NEON](https://dl.acm.org/doi/10.1145/3626232.3653258),
which is licensed under the GPL v3 license. All code except the code in
[`code/federated/Programs`](code/federated/Programs) is therefore also licensed
under the GPL v3 license. The code in
[`code/federated/Programs`](code/federated/Programs) can be used independently
of NEON, and we license it under the MIT license.
