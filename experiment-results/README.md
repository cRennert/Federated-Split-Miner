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

The files are written by [`code/conformance`](../code/conformance), which
measures the Petri net of every model against the original event log with
Rust4PM (`process_mining` 0.7.1): the alignment-based fitness averaged over the
traces (standard costs) and the escaping-edges precision on the same
alignments with the ProM preset, within 10 minutes per model.

Every file records the algorithm, its input, its parameters and the model file
it was measured on, followed by one entry per event log with the fitness, the
precision, their harmonic mean, a status and, where a value is missing, the
reason:

* `measured` — both values returned,
* `timeout` — the checker exceeded its time limit,
* `failed` — no value could be measured, e.g. because the final marking of the
  net is unreachable,
* `missing` — there is no Petri net for the model.

Of the sixty combinations, 58 are measured. Split Miner 1.0 on BPIC 19 fails,
as the final marking of its net is unreachable, and Split Miner 1.0 on BPIC 11
has no Petri net.

## `tables`

The tables of the paper, in the LaTeX the paper includes and as Markdown for
reading here:

| | |
|---|---|
| `quality.md`, `quality.tex` | fitness, precision and harmonic mean per log and setting |
| `runtimes-semi-honest-lan.md`, `runtimes-sh-lan.tex` | runtime per protocol stage, `replicated-ring`, LAN |
| `runtimes-malicious-lan.md`, `runtimes-mal-lan.tex` | runtime per protocol stage, `sy-rep-ring`, LAN |

`quality.md` and `quality.tex` are written by
[`code/conformance`](../code/conformance) together with the files in
`conformance`, so they cannot drift from them; in both, the highest harmonic
mean per event log is set in bold. The runtime tables come from the
computation reports of a secure run.

## `evaluation-reports`

Empty. The runtime tables are generated from the computation reports that a
secure evaluation produces, which are not part of this repository. Place them
under `evaluation-reports/split-miner` and run `code/federated/plot-eval.py` to regenerate
them; see the top-level README.
