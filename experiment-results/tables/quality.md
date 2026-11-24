### Alignment-based fitness, ETC-precision and their harmonic mean

Measured with Rust4PM (`process_mining` 0.7.1) on the original event logs: the alignment fitness averaged over the traces, and the escaping-edges precision on the alignments with the ProM preset. A dash marks a value that was not measured; the status column of each setting says why.

#### Harmonic mean per setting

| Event log | Split Miner 1.0 | FSM (original log) | FSM (partial logs) | IMd tau=0 | IMd tau=0.1 | IMd tau=0.2 |
|---|--:|--:|--:|--:|--:|--:|
| BPIC 11: Hospital Log | -- | **0.167** | 0.152 | 0.008 | 0.100 | 0.093 |
| BPIC 12: Loan applications | 0.637 | 0.671 | 0.691 | 0.298 | 0.635 | **0.703** |
| BPIC 13: Open Problems | 0.744 | **0.948** | **0.948** | 0.727 | 0.727 | 0.727 |
| BPIC 13: Closed Problems | **0.941** | 0.926 | 0.926 | 0.796 | 0.931 | 0.931 |
| BPIC 13: Incidents | **0.932** | 0.921 | 0.921 | 0.827 | 0.737 | 0.737 |
| BPIC 17: Offer log | **0.990** | **0.990** | **0.990** | 0.977 | 0.977 | 0.977 |
| BPIC 18: Payment process | **0.527** | 0.439 | 0.421 | 0.128 | 0.321 | 0.338 |
| BPIC 19: Purchase order handling | -- | **0.889** | 0.878 | 0.467 | 0.732 | 0.636 |
| Sepsis Cases | **0.761** | 0.739 | 0.726 | 0.608 | 0.392 | 0.439 |
| Road Traffic Fines | **0.996** | 0.978 | 0.978 | 0.669 | 0.847 | 0.847 |

#### Split Miner 1.0

Split Miner 1.0, original log, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | missing |
| BPIC 12: Loan applications | 0.823 | 0.519 | 0.637 | measured |
| BPIC 13: Open Problems | 0.811 | 0.687 | 0.744 | measured |
| BPIC 13: Closed Problems | 0.941 | 0.940 | 0.941 | measured |
| BPIC 13: Incidents | 0.913 | 0.952 | 0.932 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | 0.866 | 0.379 | 0.527 | measured |
| BPIC 19: Purchase order handling | -- | -- | -- | failed |
| Sepsis Cases | 0.729 | 0.797 | 0.761 | measured |
| Road Traffic Fines | 0.993 | 1.000 | 0.996 | measured |

#### FSM (original log)

Federated Split Miner, original log, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 0.864 | 0.092 | 0.167 | measured |
| BPIC 12: Loan applications | 0.961 | 0.516 | 0.671 | measured |
| BPIC 13: Open Problems | 0.939 | 0.958 | 0.948 | measured |
| BPIC 13: Closed Problems | 0.988 | 0.871 | 0.926 | measured |
| BPIC 13: Incidents | 0.977 | 0.871 | 0.921 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | 0.985 | 0.282 | 0.439 | measured |
| BPIC 19: Purchase order handling | 0.988 | 0.808 | 0.889 | measured |
| Sepsis Cases | 0.764 | 0.716 | 0.739 | measured |
| Road Traffic Fines | 0.996 | 0.960 | 0.978 | measured |

#### FSM (partial logs)

Federated Split Miner, three partial logs, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 0.794 | 0.084 | 0.152 | measured |
| BPIC 12: Loan applications | 0.951 | 0.543 | 0.691 | measured |
| BPIC 13: Open Problems | 0.939 | 0.958 | 0.948 | measured |
| BPIC 13: Closed Problems | 0.988 | 0.871 | 0.926 | measured |
| BPIC 13: Incidents | 0.977 | 0.871 | 0.921 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | 0.982 | 0.268 | 0.421 | measured |
| BPIC 19: Purchase order handling | 0.986 | 0.792 | 0.878 | measured |
| Sepsis Cases | 0.789 | 0.673 | 0.726 | measured |
| Road Traffic Fines | 0.996 | 0.960 | 0.978 | measured |

#### IMd tau=0

Inductive Miner directly-follows, original log, tau = 0.0.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 1.000 | 0.004 | 0.008 | measured |
| BPIC 12: Loan applications | 1.000 | 0.175 | 0.298 | measured |
| BPIC 13: Open Problems | 0.977 | 0.579 | 0.727 | measured |
| BPIC 13: Closed Problems | 1.000 | 0.661 | 0.796 | measured |
| BPIC 13: Incidents | 1.000 | 0.705 | 0.827 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | 1.000 | 0.068 | 0.128 | measured |
| BPIC 19: Purchase order handling | 1.000 | 0.305 | 0.467 | measured |
| Sepsis Cases | 1.000 | 0.437 | 0.608 | measured |
| Road Traffic Fines | 1.000 | 0.502 | 0.669 | measured |

#### IMd tau=0.1

Inductive Miner directly-follows, original log, tau = 0.1.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 0.945 | 0.053 | 0.100 | measured |
| BPIC 12: Loan applications | 0.909 | 0.488 | 0.635 | measured |
| BPIC 13: Open Problems | 0.977 | 0.579 | 0.727 | measured |
| BPIC 13: Closed Problems | 0.993 | 0.876 | 0.931 | measured |
| BPIC 13: Incidents | 0.993 | 0.586 | 0.737 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | 0.970 | 0.192 | 0.321 | measured |
| BPIC 19: Purchase order handling | 0.937 | 0.601 | 0.732 | measured |
| Sepsis Cases | 0.991 | 0.244 | 0.392 | measured |
| Road Traffic Fines | 0.854 | 0.841 | 0.847 | measured |

#### IMd tau=0.2

Inductive Miner directly-follows, original log, tau = 0.2.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 0.853 | 0.049 | 0.093 | measured |
| BPIC 12: Loan applications | 0.996 | 0.544 | 0.703 | measured |
| BPIC 13: Open Problems | 0.977 | 0.579 | 0.727 | measured |
| BPIC 13: Closed Problems | 0.993 | 0.876 | 0.931 | measured |
| BPIC 13: Incidents | 0.993 | 0.586 | 0.737 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | 0.759 | 0.217 | 0.338 | measured |
| BPIC 19: Purchase order handling | 0.938 | 0.482 | 0.636 | measured |
| Sepsis Cases | 0.900 | 0.290 | 0.439 | measured |
| Road Traffic Fines | 0.854 | 0.841 | 0.847 | measured |
