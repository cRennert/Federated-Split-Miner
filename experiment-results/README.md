# Experiment results

## `conformance`

Alignment-based fitness and ETC-precision for every discovered model, organised
by the combination of algorithm, parameters and data. One file per combination:

| File | Algorithm | Input | Parameters |
|---|---|---|---|
| `split-miner-1.0.json` | Split Miner 1.0 | original log | ε = 0.1, η = 0.4 |
| `federated-split-miner_original.json` | our Split Miner variant | original log | ε = 0.1, η = 0.4 |
| `federated-split-miner_federated.json` | our Split Miner variant | three partial logs | ε = 0.1, η = 0.4 |
| `inductive-miner-df_tau0.0.json` | Inductive Miner directly-follows | original log | τ = 0 |
| `inductive-miner-df_tau0.1.json` | Inductive Miner directly-follows | original log | τ = 0.1 |
| `inductive-miner-df_tau0.2.json` | Inductive Miner directly-follows | original log | τ = 0.2 |

Every file records the algorithm, its input, its parameters and the model file
it was measured on, followed by one entry per event log with the fitness, the
precision, their harmonic mean, and a status:

* `measured` — both values returned,
* `timeout` — the checker exceeded its time limit,
* `failed` — the checker raised,
* `undefined` — a fitness was returned but the precision was not, which happens
  for a model permissive enough that the measure degenerates.

Of the sixty combinations, 39 are fully measured, 2 return a fitness only, and
19 return nothing.

### `conformance/raw`

The three files as the conformance checker produced them, kept for provenance.
They are organised by measurement batch rather than by what was measured: a base
sweep over all six settings, a re-measurement of our two settings on the models
discovered after the η-percentile was corrected, and two Inductive Miner values
the base sweep did not return. The files above merge the three, with the
re-measurements taking precedence.

## `tables`

The tables of the paper, in the LaTeX the paper includes and as Markdown for
reading here:

| | |
|---|---|
| `quality.md`, `quality.tex` | fitness, precision and harmonic mean per log and setting |
| `runtimes-semi-honest-lan.md`, `runtimes-sh-lan.tex` | runtime per protocol stage, `replicated-ring`, LAN |
| `runtimes-malicious-lan.md`, `runtimes-mal-lan.tex` | runtime per protocol stage, `sy-rep-ring`, LAN |

`quality.md` is generated from the files in `conformance`, so it cannot drift
from them; the runtime tables come from the computation reports of a secure run.

## `evaluation-reports`

Empty. The runtime tables are generated from the computation reports that a
secure evaluation produces, which are not part of this repository. Place them
under `evaluation-reports/split-miner` and run `code/federated/plot-eval.py` to regenerate
them; see the top-level README.
