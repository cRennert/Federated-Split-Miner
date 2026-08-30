# Experiment inputs

The evaluation runs on ten publicly available real-life event logs. Each of them
is split across three organizations, and it is those **partial** logs that the
federated protocols read — no party ever sees a complete log.

## Partial logs

[`partial-logs`](partial-logs) holds, per event log, the three partial logs of
the split used in the paper, named `<log>_3_0.xes.gz`, `<log>_3_1.xes.gz` and
`<log>_3_2.xes.gz`, together with the manifest that records the log, the number
of organizations, the seed, and the number of activities.

All ten splits are included, BPIC 18 among them, whose three partial logs
account for 145 MB of the 186 MB here.

The Sepsis log is split twice, so there are eleven folders for ten logs.
`Sepsis_Cases` is the random split that every other log also gets and that the
quality table reports; `sepsis_by_department` is a second split of the same log
along the departments that record it, and it is the one the runtime tables
report as *Sepsis Cases (by department)*.

## Original logs

[`original-logs`](original-logs) holds the ten logs the splitter reads, 183 MB
in total. They are the published logs, unmodified, and they are redistributed
here so that the evaluation can be rerun without fetching anything; each of them
is available from the 4TU.ResearchData repository under the license its
depositors chose, and it is that license, not ours, that governs them.

**`BPI_Challenge_2018.xes.gz` is stored in two parts.** At 151 MB it is the one
file that a plain Git repository cannot carry --- GitHub refuses anything over
100 MB without Git LFS --- so it is committed as `BPI_Challenge_2018.xes.gz.part00`
and `.part01`, of 80 MB and 71 MB. Join them before any run that reads the
original logs:

```bash
python experiment-inputs/assemble_logs.py
```

The script concatenates the parts, checks the result against the SHA-256 in
`BPI_Challenge_2018.xes.gz.sha256`, and does nothing if the log is already
there and matches. The joined file is listed in `.gitignore`, so it never goes
back into the repository. Until it is joined, the splitter skips BPIC 18 and
`generate_reference_models.py` reports its original log as missing and discovers
the federated model only.

```
original-logs/
  BPI_Challenge_2012.xes.gz
  BPI_Challenge_2013_closed_problems.xes.gz
  BPI_Challenge_2013_incidents.xes.gz
  BPI_Challenge_2013_open_problems.xes.gz
  BPI_Challenge_2018.xes.gz
  BPI_Challenge_2019.xes.gz
  BPIC17 - Offer log.xes.gz
  Hospital_log.xes.gz
  Sepsis Cases.xes.gz
  road_traffic_fine_management.xes.gz
```

`Hospital_log.xes.gz` is the BPI Challenge 2011 hospital log. The two names
carrying spaces are the names the logs are published under; the splitter maps
them to `BPIC17_Offer_log` and `Sepsis_Cases`.

## Reproducing the splits

```bash
uv run python experiment-inputs/split_logs.py
uv run python experiment-inputs/split_sepsis_by_department.py
```

The first produces the random split of all ten logs, the second the departmental
split of the Sepsis log. The two are independent of each other.

### The random split

The splitter assigns activities and events to organizations in two steps, which
the paper describes: an activity label goes to one organization with probability
0.6, to two with probability 0.3 and to all three with probability 0.1, and each
event is then handed to one of the organizations that own its activity label. If
an organization would end up with no label at all, it receives one, so that no
partial log is empty.

Both steps draw from a generator seeded with 20260824, and the seed is written
into the manifest beside every split.

That one generator is shared by all ten logs and is drawn from in the order the
folder sorts, so a split depends on every log processed before it. Reproducing
the bundled partial logs byte for byte therefore requires a **complete** run:
all ten logs present in `original-logs`, so with BPIC 18 joined by
`assemble_logs.py` first, and no log name given on the command line. Passing names
restricts which logs are written but not which draws are made, so
`split_logs.py Sepsis_Cases` produces a *valid* split of Sepsis and not the one
bundled here. Use it to inspect what the splitter does; use a complete run to
reproduce.

### The departmental split

`split_sepsis_by_department.py` draws nothing at all. The three organizations
are the departments of the hospital, and each activity label is recorded by
exactly one of them:

| Organization | Activities | Events |
|---|---|--:|
| 1, Emergency Room | ER Registration, ER Triage, ER Sepsis Triage | 3,152 |
| 2, Medical Department | Admission IC, Admission NC, CRP, IV Antibiotics, IV Liquid, LacticAcid, Leucocytes | 10,986 |
| 3, Financial Department | Release A--E, Return ER | 1,076 |

Every event carries an activity label and every label has one owner, so the
split is a function of the log alone and reruns reproduce it exactly. It is the
split behind the collaboration model of the paper, where the three pools are
these three departments.
