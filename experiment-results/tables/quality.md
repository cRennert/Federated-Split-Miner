### Alignment-based fitness, ETC-precision and their harmonic mean

A dash marks a measurement the conformance checker did not return, either because it exceeded the time limit or because it failed.

#### Harmonic mean per setting

| Event log | Split Miner 1.0 | FSM (original log) | FSM (partial logs) | IMd tau=0 | IMd tau=0.1 | IMd tau=0.2 |
|---|--:|--:|--:|--:|--:|--:|
| BPIC 11: Hospital Log | -- | -- | -- | -- | -- | -- |
| BPIC 12: Loan applications | 0.702 | 0.675 | 0.692 | 0.298 | -- | 0.704 |
| BPIC 13: Open Problems | 0.789 | 0.948 | 0.948 | 0.826 | 0.832 | 0.824 |
| BPIC 13: Closed Problems | 0.936 | 0.926 | 0.926 | 0.830 | 0.931 | 0.931 |
| BPIC 13: Incidents | 0.931 | 0.923 | 0.923 | 0.827 | 0.810 | 0.811 |
| BPIC 17: Offer log | 0.990 | 0.990 | 0.990 | 0.977 | 0.977 | 0.977 |
| BPIC 18: Payment process | -- | -- | -- | -- | -- | -- |
| BPIC 19: Purchase order handling | -- | -- | -- | -- | -- | -- |
| Sepsis Cases | 0.763 | 0.736 | -- | -- | 0.484 | 0.462 |
| Road Traffic Fines | 0.996 | 0.977 | 0.977 | 0.669 | 0.878 | 0.878 |

#### Split Miner 1.0

Split Miner 1.0, original log, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | 1.000 | -- | -- | undefined |
| BPIC 12: Loan applications | 0.823 | 0.611 | 0.702 | measured |
| BPIC 13: Open Problems | 0.811 | 0.768 | 0.789 | measured |
| BPIC 13: Closed Problems | 0.941 | 0.930 | 0.936 | measured |
| BPIC 13: Incidents | 0.913 | 0.949 | 0.931 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | -- | -- | -- | failed |
| BPIC 19: Purchase order handling | 1.000 | -- | -- | undefined |
| Sepsis Cases | 0.729 | 0.802 | 0.763 | measured |
| Road Traffic Fines | 0.993 | 1.000 | 0.996 | measured |

#### FSM (original log)

Federated Split Miner, original log, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | failed |
| BPIC 12: Loan applications | 0.961 | 0.520 | 0.675 | measured |
| BPIC 13: Open Problems | 0.939 | 0.959 | 0.948 | measured |
| BPIC 13: Closed Problems | 0.988 | 0.871 | 0.926 | measured |
| BPIC 13: Incidents | 0.977 | 0.874 | 0.923 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | -- | -- | -- | timeout |
| BPIC 19: Purchase order handling | -- | -- | -- | timeout |
| Sepsis Cases | 0.764 | 0.710 | 0.736 | measured |
| Road Traffic Fines | 0.996 | 0.958 | 0.977 | measured |

#### FSM (partial logs)

Federated Split Miner, three partial logs, epsilon = 0.1, eta = 0.4.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | failed |
| BPIC 12: Loan applications | 0.951 | 0.544 | 0.692 | measured |
| BPIC 13: Open Problems | 0.939 | 0.959 | 0.948 | measured |
| BPIC 13: Closed Problems | 0.988 | 0.871 | 0.926 | measured |
| BPIC 13: Incidents | 0.977 | 0.874 | 0.923 | measured |
| BPIC 17: Offer log | 0.980 | 1.000 | 0.990 | measured |
| BPIC 18: Payment process | -- | -- | -- | timeout |
| BPIC 19: Purchase order handling | -- | -- | -- | timeout |
| Sepsis Cases | -- | -- | -- | timeout |
| Road Traffic Fines | 0.996 | 0.958 | 0.977 | measured |

#### IMd tau=0

Inductive Miner directly-follows, original log, tau = 0.0.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | failed |
| BPIC 12: Loan applications | 1.000 | 0.175 | 0.298 | measured |
| BPIC 13: Open Problems | 0.977 | 0.716 | 0.826 | measured |
| BPIC 13: Closed Problems | 1.000 | 0.709 | 0.830 | measured |
| BPIC 13: Incidents | 1.000 | 0.705 | 0.827 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | -- | -- | -- | failed |
| BPIC 19: Purchase order handling | -- | -- | -- | failed |
| Sepsis Cases | -- | -- | -- | failed |
| Road Traffic Fines | 1.000 | 0.502 | 0.669 | measured |

#### IMd tau=0.1

Inductive Miner directly-follows, original log, tau = 0.1.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | failed |
| BPIC 12: Loan applications | -- | -- | -- | failed |
| BPIC 13: Open Problems | 0.977 | 0.724 | 0.832 | measured |
| BPIC 13: Closed Problems | 0.993 | 0.876 | 0.931 | measured |
| BPIC 13: Incidents | 0.993 | 0.683 | 0.810 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | -- | -- | -- | failed |
| BPIC 19: Purchase order handling | -- | -- | -- | failed |
| Sepsis Cases | 0.991 | 0.320 | 0.484 | measured |
| Road Traffic Fines | 0.854 | 0.903 | 0.878 | measured |

#### IMd tau=0.2

Inductive Miner directly-follows, original log, tau = 0.2.

| Event log | fitness | precision | F1 | status |
|---|--:|--:|--:|---|
| BPIC 11: Hospital Log | -- | -- | -- | failed |
| BPIC 12: Loan applications | 0.996 | 0.544 | 0.704 | measured |
| BPIC 13: Open Problems | 0.977 | 0.712 | 0.824 | measured |
| BPIC 13: Closed Problems | 0.993 | 0.876 | 0.931 | measured |
| BPIC 13: Incidents | 0.993 | 0.686 | 0.811 | measured |
| BPIC 17: Offer log | 1.000 | 0.955 | 0.977 | measured |
| BPIC 18: Payment process | -- | -- | -- | failed |
| BPIC 19: Purchase order handling | -- | -- | -- | failed |
| Sepsis Cases | 0.900 | 0.311 | 0.462 | measured |
| Road Traffic Fines | 0.854 | 0.903 | 0.878 | measured |
