# Experiment outputs

One folder per event log, each holding the process models that the evaluation
compares:

| File | Discovered by |
|---|---|
| `FederatedSplitMiner_original.bpmn` | our Split Miner variant on the original log |
| `FederatedSplitMiner_federated.bpmn` | our Split Miner variant on the three partial logs |
| `SplitMiner1.0.bpmn` | the original Split Miner 1.0 |
| `dfg_im_00.pnml`, `dfg_im_01.pnml`, `dfg_im_02.pnml` | the Inductive Miner directly-follows at the noise thresholds 0, 0.1 and 0.2 |

The first two differ only in whether the algorithm saw one log or three partial
ones. They are identical wherever the timestamps of a log fix the order of every
case; where two events of one case share a timestamp, the merged order of a
federated run is decided by the index of the contributing organization while the
original file keeps the order in which its events happen to be written, and the
two models can then differ.

`code/reference/generate_reference_models.py` rediscovers the first two and reports, per
log, how far each is from the Split Miner 1.0 model on the same input. The
comparison is made on the filtered directly-follows graph rather than on the
BPMN syntax, since two models that induce the same graph describe the same
control flow however their gateways happen to be nested.
