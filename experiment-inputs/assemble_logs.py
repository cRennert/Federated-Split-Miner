"""Reassemble the original logs that are stored in parts.

A single file over 100 MB cannot be pushed to GitHub without Git LFS, so the
BPI Challenge 2018 log is committed as ``<name>.part00``, ``<name>.part01``,
... instead. This script concatenates them back and checks the result against
the SHA-256 recorded in ``<name>.sha256``.

    python experiment-inputs/assemble_logs.py

It is safe to run repeatedly: a log that is already assembled and whose digest
matches is left alone.
"""
import hashlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "original-logs")
CHUNK = 1 << 20


def digest(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def assemble(name, parts, expected):
    target = os.path.join(IN_DIR, name)
    if os.path.exists(target):
        if digest(target) == expected:
            print("  %-32s already assembled" % name)
            return True
        print("  %-32s present but does not match its digest, rebuilding" % name)

    total = sum(os.path.getsize(p) for p in parts)
    print("  %-32s joining %d parts, %.1f MB" % (name, len(parts), total / 1048576))
    tmp = target + ".partial"
    with io.open(tmp, "wb") as out:
        for p in parts:
            with io.open(p, "rb") as f:
                for block in iter(lambda: f.read(CHUNK), b""):
                    out.write(block)

    got = digest(tmp)
    if got != expected:
        os.remove(tmp)
        print("  %-32s FAILED: digest %s, expected %s" % (name, got[:16], expected[:16]))
        return False
    os.replace(tmp, target)
    print("  %-32s ok" % name)
    return True


def main():
    if not os.path.isdir(IN_DIR):
        sys.exit("no original-logs folder next to this script")

    names = sorted({f.rsplit(".part", 1)[0] for f in os.listdir(IN_DIR)
                    if ".part" in f and f.rsplit(".part", 1)[1].isdigit()})
    if not names:
        print("  nothing to assemble")
        return 0

    failed = 0
    for name in names:
        parts = sorted(os.path.join(IN_DIR, f) for f in os.listdir(IN_DIR)
                       if f.startswith(name + ".part")
                       and f.rsplit(".part", 1)[1].isdigit())
        sha = os.path.join(IN_DIR, name + ".sha256")
        if not os.path.exists(sha):
            print("  %-32s no .sha256 beside its parts, skipped" % name)
            failed += 1
            continue
        expected = io.open(sha, encoding="utf-8").read().split()[0]
        failed += not assemble(name, parts, expected)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
