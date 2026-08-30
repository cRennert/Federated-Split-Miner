# Implementation

The implementation comes in two halves that share no code, only the algorithm
they realize. Every claim of the paper that involves running something involves
exactly one of them.

## `reference` --- the cleartext reference implementation

Our Split Miner variant written as ordinary Python over a plain event log, with
the design decisions of Section 4 and none of the secure computation. It is the
implementation the protocols are checked against, and the one that produces the
models in [`../experiment-outputs`](../experiment-outputs).

| File | |
|---|---|
| `reference_split_miner.py` | the variant itself, from the directly-follows graph to the BPMN model |
| `generate_reference_models.py` | rediscovers every model of the evaluation and compares it against Split Miner 1.0 |

This half imports neither MP-SPDZ nor the NEONIK runtime; `pm4py` is all it
needs, so it runs anywhere, including on platforms where the environment of the
other half does not install:

```bash
python reference/generate_reference_models.py
```

## `federated` --- the secure implementation

The protocols of Section 5 and everything that drives them. Running any of it
needs the full environment, MP-SPDZ, and a Linux kernel with `netem`.

| File | |
|---|---|
| `Programs/split_miner.mpc` | entry point of a secure run |
| `Programs/Dependencies/split_miner.py` | the protocols of Section 5 |
| `Programs/Dependencies/dfg.py` | directly-follows graph construction |
| `Programs/Dependencies/demux.py` | the demultiplex building block |
| `split_miner_socket.py`, `socket_client.py` | turn the revealed artifacts into a BPMN collaboration |
| `main.py` | driver of a single computation; stages the partial logs it needs from `experiment-inputs` |
| `generate_experiments.py`, `neonik-project.yml` | the experiment specifications and their parameters |
| `extract_model_outputs.py` | pulls the models out of the computation reports |
| `plot-eval.py` | turns computation reports into figures and tables |

The names the protocols carry in the paper are listed in the top-level
[README](../README.md#protocol-names).

## Why they are separate

The reference implementation is the readable statement of what the variant
computes; the federated one is the same computation expressed so that no party
learns more than the final model. Keeping them apart makes it checkable that the
first never reaches into the second, and it lets a reviewer reproduce every
discovered model without installing the secure-computation stack at all.
