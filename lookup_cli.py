#!/usr/bin/env python3
"""
lookup_cli -- interactive front end for lookup.py. A testing tool: paste a
string, see what the classifier thinks it is, then see what it found.

    python lookup_cli.py "Saturn VI"
    python lookup_cli.py "Io" "J I"          several keys naming ONE body
    python lookup_cli.py --each 73P-C 1P 90  each argument its own query
    python lookup_cli.py                     queries from stdin, one per line

    --all         show readings that matched nothing too
    --json        machine-readable
    --limit N     rows per reading (default 10)
    --master P    a specific master_lookup.csv

SEVERAL KEYS = PRIMACY. Param 0 decides which body; later params may only
narrow it. One pointing elsewhere is logged as a disagreement, one pointing
nowhere as unresolved, and neither empties the answer. If param 0 itself
does not resolve, primacy passes to the first param that does.
"""

import argparse
import json
import sys

import lookup
from lookup import brief, clean


def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=True, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="*", help="identifier(s)")
    p.add_argument("--each", action="store_true",
                   help="treat each argument as a separate query")
    p.add_argument("--all", action="store_true",
                   help="also print readings that matched nothing")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--master", help="path to master_lookup.db or .csv")
    p.add_argument("--csv", action="store_true",
                   help="force the CSV backend even if a .db exists")
    return p.parse_args(argv)


def show_readings(guesses, show_all, indent="   "):
    for g in guesses:
        if not g.rows and not show_all:
            continue
        mark = "*" if g.rows else " "
        yield f"{indent}{mark} [{g.kind}] {g.reading}", g


def run_one(q, store, show_all, limit):
    guesses, idx = lookup.resolve(q, store, show_all)
    c = clean(q)
    print(f"\n> {q}" + (f"   [cleaned: {c}]" if c != q.strip() else ""))
    printed = False
    for line, g in show_readings(guesses, show_all, indent=" "):
        print(line)
        printed = True
        for r in g.rows[:limit]:
            print(f"      {brief(r)}")
        if len(g.rows) > limit:
            print(f"      ... {len(g.rows) - limit} more")
        if not g.rows:
            print("      no match")
    if not idx:
        tried = ", ".join(dict.fromkeys(g.kind for g in guesses)) or "nothing"
        print(f"  no match. tried: {tried}")
    elif sum(1 for g in guesses if g.rows) > 1 and printed:
        print(f"  AMBIGUOUS: {sum(1 for g in guesses if g.rows)} readings matched")


def run_multi(keys, store, show_all, limit):
    print("\n> " + "  +  ".join(repr(k) for k in keys))
    answer, notes = None, []
    for n, k in enumerate(keys):
        c = clean(k)
        guesses, idx = lookup.resolve(k, store, show_all)
        print(f"  param {n} {k!r}"
              + (f" [cleaned: {c}]" if c != k.strip() else ""))
        for line, _ in show_readings(guesses, show_all):
            print(line)
        if not idx:
            notes.append(f"param {n} {k!r}: unresolved, ignored")
            print("      unresolved -- not in the table, or not parsed")
        elif answer is None:
            answer = idx
            print(f"      <- PRIMARY, establishes {len(idx)} candidate row(s)")
            if n:
                notes.append(f"param 0 did not resolve; primacy passed to "
                             f"param {n} {k!r}")
        else:
            both = answer & idx
            if not both:
                notes.append(f"param {n} {k!r}: DISAGREES with the primary, "
                             f"ignored")
                print("      DISAGREES with the primary key -- ignored")
            elif both == answer:
                print("      agrees, no narrowing")
            else:
                print(f"      narrows {len(answer)} -> {len(both)}")
                answer = both

    if answer is None:
        print("\n  = nothing. no key resolved")
    else:
        print(f"\n  = {len(answer)} row(s)")
        for i in sorted(answer)[:limit]:
            print(f"      {brief(store[i])}")
        if len(answer) > limit:
            print(f"      ... {len(answer) - limit} more")
    for line in notes:
        print(f"  ! {line}")


def main(argv=None):
    args = parse_args(argv)
    try:
        store = lookup.open_store(args.master, args.csv)
    except lookup.LookupDataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    queries = args.query
    if not queries:
        if sys.stdin.isatty():
            print(f"{len(store)} rows via {store.backend}. "
                  f"one query per line, Ctrl-C to quit.")
        queries = [l.strip() for l in sys.stdin if l.strip()]

    if args.json:
        out = []
        for q in queries:
            guesses, _ = lookup.resolve(q, store, args.all)
            out.append({"query": q, "readings": [
                {"kind": g.kind, "reading": g.reading,
                 "matches": [{k: v for k, v in m.items() if k != "_i"}
                             for m in g.rows]}
                for g in guesses if g.rows or args.all]})
        print(json.dumps(out, indent=2))
        return 0

    if len(queries) > 1 and not args.each:
        run_multi(queries, store, args.all, args.limit)
    else:
        for q in queries:
            run_one(q, store, args.all, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())