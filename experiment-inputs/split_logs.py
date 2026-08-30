"""Split real-life event logs across P organizations by activity label.

Two steps, as described in the paper:

  Step 1 -- the activity-to-organization mapping. Every activity label is
  assigned to one organization with probability 0.6, to two with probability
  0.3, and to all three with probability 0.1; the organizations are drawn
  uniformly without replacement. If that leaves an organization without a single
  label, one label is drawn and the organization is added to its owner set, so
  that no partial log comes out empty.

  Step 2 -- the events. Every event is assigned uniformly at random to one of
  the organizations that own its activity label.

The logs are streamed trace by trace: BPI Challenge 2018 is 151 MB compressed
and does not fit comfortably in memory. Traces and events are re-serialized from
the parse tree, so nested attributes survive; the source formatting does not
matter, which it does for a line-based reader (BPIC17 is a single line).
"""
import gzip, io, json, os, random, re, sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

P = 3
SEED = 20260824
_HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(_HERE, "original-logs")
OUT_DIR = os.path.join(_HERE, "partial-logs")
SKIP = re.compile(r"^sample_")
NAME_FIX = {"BPIC17 - Offer log": "BPIC17_Offer_log", "Sepsis Cases": "Sepsis_Cases"}

local = lambda t: t.split("}")[-1]

def ser(el, indent):
    tag = local(el.tag)
    attrs = "".join(" %s=%s" % (local(k), quoteattr(v)) for k, v in el.attrib.items())
    kids = list(el)
    if not kids:
        return "%s<%s%s/>\n" % (indent, tag, attrs)
    out = ["%s<%s%s>\n" % (indent, tag, attrs)]
    out += [ser(c, indent + "\t") for c in kids]
    out.append("%s</%s>\n" % (indent, tag))
    return "".join(out)

def act_of(ev):
    for c in ev:
        if c.get("key") == "concept:name":
            return c.get("value")
    return None

def traces(path):
    """Stream (trace_element, root_attrs, header_elements_once)."""
    with gzip.open(path, "rb") as f:
        ctx = ET.iterparse(f, events=("start", "end"))
        _, root = next(ctx)
        stack, header, seen_trace = ["log"], [], False
        yield ("root", dict(root.attrib), None)
        for ev, el in ctx:
            tag = local(el.tag)
            if ev == "start":
                stack.append(tag); continue
            stack.pop()
            parent = stack[-1] if stack else None
            if tag == "trace" and parent == "log":
                if not seen_trace:
                    seen_trace = True
                    yield ("header", header, None)
                yield ("trace", el, None)
                el.clear()
            elif parent == "log" and not seen_trace:
                header.append(ser(el, "\t")); el.clear()

def scan_activities(path):
    acts, n = set(), 0
    for kind, payload, _ in traces(path):
        if kind != "trace": continue
        for c in payload:
            if local(c.tag) == "event":
                a = act_of(c)
                if a is not None: acts.add(a)
                n += 1
    return acts, n

def build_mapping(acts, rng):
    owners = {}
    for a in sorted(acts):
        r = rng.random()
        k = 1 if r < 0.6 else (2 if r < 0.9 else 3)
        owners[a] = sorted(rng.sample(range(P), k))
    covered = {o for v in owners.values() for o in v}
    repaired = []
    for o in range(P):
        if o not in covered:
            a = rng.choice(sorted(acts))
            if o not in owners[a]:
                owners[a] = sorted(owners[a] + [o]); repaired.append((a, o))
    return owners, repaired

def split(path, out_dir, base, owners, rng):
    outs = [gzip.open(os.path.join(out_dir, "%s_%d_%d.xes.gz" % (base, P, i)), "wt",
                      encoding="utf-8") for i in range(P)]
    counts = [0]*P; tr_out = [0]*P; total = 0
    try:
        for kind, payload, _ in traces(path):
            if kind == "root":
                attrs = "".join(" %s=%s" % (local(k), quoteattr(v)) for k, v in payload.items())
                for o in outs:
                    o.write('<?xml version="1.0" encoding="UTF-8" ?>\n<log%s>\n' % attrs)
            elif kind == "header":
                for o in outs: o.write("".join(payload))
            else:
                el = payload
                head = [ser(c, "\t\t") for c in el if local(c.tag) != "event"]
                buf = [[] for _ in range(P)]
                for c in el:
                    if local(c.tag) != "event": continue
                    o = rng.choice(owners.get(act_of(c), list(range(P))))
                    buf[o].append(ser(c, "\t\t")); counts[o] += 1; total += 1
                tattr = "".join(" %s=%s" % (local(k), quoteattr(v)) for k, v in el.attrib.items())
                for i, o in enumerate(outs):
                    if buf[i]:
                        o.write("\t<trace%s>\n" % tattr); o.write("".join(head))
                        o.write("".join(buf[i])); o.write("\t</trace>\n"); tr_out[i] += 1
        for o in outs: o.write("</log>\n")
    finally:
        for o in outs: o.close()
    return counts, tr_out, total

def main():
    rng = random.Random(SEED)
    os.makedirs(OUT_DIR, exist_ok=True)
    names = sorted(f for f in os.listdir(IN_DIR) if f.endswith(".xes.gz") and not SKIP.match(f))
    only = sys.argv[1:] or None
    for fn in names:
        stem = fn[:-len(".xes.gz")]; base = NAME_FIX.get(stem, stem)
        if only and base not in only: continue
        out_dir = os.path.join(OUT_DIR, base); os.makedirs(out_dir, exist_ok=True)
        acts, _ = scan_activities(os.path.join(IN_DIR, fn))
        owners, repaired = build_mapping(acts, rng)
        counts, tr_out, total = split(os.path.join(IN_DIR, fn), out_dir, base, owners, rng)
        per_org = [sum(1 for a, v in owners.items() if o in v) for o in range(P)]
        json.dump({"log": base, "P": P, "seed": SEED, "activities": len(acts),
                   "labels_per_organization": per_org,
                   "repaired_empty_organizations": repaired,
                   "events": total, "events_per_organization": counts,
                   "traces_per_organization": tr_out,
                   "mapping": {a: v for a, v in sorted(owners.items())}},
                  io.open(os.path.join(out_dir, "%s_%d_mapping.json" % (base, P)), "w",
                          encoding="utf-8"), indent=2, ensure_ascii=False)
        print("%-36s %4d labels %9d events | labels/org %s | events/org %s%s"
              % (base, len(acts), total, per_org, counts,
                 "  repaired %s" % repaired if repaired else ""), flush=True)

if __name__ == "__main__":
    main()
