"""Split the Sepsis Cases log across the three departments that record it.

This is the second split of the Sepsis log in the evaluation and the one the
runtime tables report as "Sepsis Cases (by department)". Unlike ``split_logs.py``
it draws nothing: the three organizations are the departments of the hospital,
and every activity label belongs to exactly one of them.

    python experiment-inputs/split_sepsis_by_department.py
"""
import io
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_logs as S  # noqa: E402

BASE = "sepsis_by_department"
IN = os.path.join(S.IN_DIR, "Sepsis Cases.xes.gz")
OUT = os.path.join(S.OUT_DIR, BASE)

# Organization 1 is the emergency room and organization 3 the financial
# department; everything else is recorded by the medical department, which is
# organization 2.
EMERGENCY_ROOM = ["ER Registration", "ER Triage", "ER Sepsis Triage"]
FINANCIAL = ["Release A", "Release B", "Release C", "Release D", "Release E",
             "Return ER"]
ORGANIZATIONS = ["Emergency Room", "Medical Department", "Financial Department"]


def build_mapping(acts):
    """Every label to exactly one department, the medical one taking the rest."""
    named = set(EMERGENCY_ROOM) | set(FINANCIAL)
    unknown = named - acts
    if unknown:
        raise ValueError(f"named activities that the log does not contain: "
                         f"{sorted(unknown)}")
    owners = {}
    for a in sorted(acts):
        owners[a] = [0] if a in EMERGENCY_ROOM else ([2] if a in FINANCIAL else [1])
    return owners


def main():
    if not os.path.isfile(IN):
        sys.exit(f"{IN} is missing; see experiment-inputs/README.md")

    acts, n_events = S.scan_activities(IN)
    owners = build_mapping(acts)
    os.makedirs(OUT, exist_ok=True)

    # Each label has a single owner, so the choice inside split() is between one
    # candidate and the generator cannot influence the outcome.
    counts, tr_out, total = S.split(IN, OUT, BASE, owners, random.Random(0))
    if total != n_events:
        raise AssertionError(f"{total} events written, {n_events} read")

    per_org = [sum(1 for v in owners.values() if o in v) for o in range(S.P)]
    manifest = {
        "log": BASE,
        "P": S.P,
        "assignment": "by department, not random; see split_sepsis_by_department.py",
        "organizations": ORGANIZATIONS,
        "activities": len(acts),
        "labels_per_organization": per_org,
        "events": total,
        "events_per_organization": counts,
        "traces_per_organization": tr_out,
        "mapping": {a: v for a, v in sorted(owners.items())},
    }
    json.dump(manifest, io.open(os.path.join(OUT, "%s_%d_mapping.json" % (BASE, S.P)),
                                "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    print("%-28s %4d labels %9d events" % (BASE, len(acts), total))
    for o, name in enumerate(ORGANIZATIONS):
        labels = sorted(a for a, v in owners.items() if o in v)
        print("  %d %-20s %2d labels %8d events %6d traces"
              % (o + 1, name, per_org[o], counts[o], tr_out[o]))
        print("     %s" % ", ".join(labels))


if __name__ == "__main__":
    main()
