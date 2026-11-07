#!/usr/bin/env python3
"""
spkid_by_column_cli -- front end for spkid_by_column.py. Give a raw table
and the columns to identify its rows by; every row gets an spkid_m.

    python spkid_by_column_cli.py raw.csv number "prov. des." name
    python spkid_by_column_cli.py raw.csv --show
    python spkid_by_column_cli.py raw.csv number --master ..\\other\\master.csv

    FILE          the raw table
    COLUMN...     identifier columns, STRONGEST FIRST. Omit them and a
                  default order is used from whatever the file happens to
                  have. Quote any column name containing a space or a dot.

    --cols "a,b,c"
                  the same thing as one comma-separated string. Use this
                  from a .bat: one pair of quotes instead of nested ones.
    --master P    a specific master_lookup.csv (default: beside lookup.py)
    --out DIR     where the two output files go (default: beside FILE)
    --show        print every row's decision as it is made
    --no-integrity
                  skip the integrity_check.py pass, which moves a row to
                  the unresolved file when another row already claimed its
                  spkid_m
    --columns     just list the file's columns and exit

Column order IS the priority. The first column that resolves decides which
body the row is; later ones may only narrow it. A column that points at a
different body is logged as a disagreement and ignored, one that points at
nothing is logged as unresolved and ignored. Put name last -- names collide
and drift, numbers do not.

Writes FILE_resolved.csv and FILE_unresolved.csv, both carrying
resolved_spkid_m, resolved_by and resolve_notes so every decision is
auditable.
"""

import argparse
import sys

import integrity_check
import lookup
import spkid_by_column as sbc


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", help="raw table to process")
    p.add_argument("column", nargs="*",
                   help="identifier columns, strongest first")
    p.add_argument("--cols", help="identifier columns as ONE comma-separated "
                                  "string, strongest first. Easier to quote "
                                  "from a .bat than several arguments.")
    p.add_argument("--master", help="path to master_lookup.db or .csv")
    p.add_argument("--out", help="output directory (default: beside FILE)")
    p.add_argument("--show", action="store_true",
                   help="print each row's decision")
    p.add_argument("--columns", action="store_true",
                   help="list the file's columns and exit")
    p.add_argument("--no-integrity", action="store_true",
                   help="skip the integrity_check.py pass that enforces "
                        "one spkid_m per entity")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        cols, table = sbc.read_table(args.file)
        if args.columns:
            print(f"{args.file} columns:")
            for c in cols:
                print(f"  {c!r}")
            return 0
        store = lookup.open_store(args.master)
        wanted = args.column or None
        if args.cols:
            wanted = [c.strip() for c in args.cols.split(",") if c.strip()]
        use = sbc.pick_columns(cols, wanted)
    except (sbc.InputError, lookup.LookupDataError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"{args.file}: {len(table)} row(s)")
    print(f"{store.path}: {len(store):,} row(s) via {store.backend}")
    print(f"identifying by, strongest first: {', '.join(use)}")

    def on_row(r, spkid_m, by, notes):
        label = next((r.get(c) for c in ("name",) + tuple(use) if r.get(c)), "?")
        print(f"  {spkid_m or '-':>12}  {label:24} {by}"
              + (f"   ! {'; '.join(notes)}" if notes else ""))

    stats = sbc.assign_table(table, store, use, on_row if args.show else None)
    hit_p, miss_p = sbc.write_split(args.file, cols, table, args.out)

    print(f"\n{stats['resolved']} resolved -> {hit_p}")
    print(f"{stats['unresolved']} unresolved -> {miss_p}")
    if stats["conflicts"]:
        print(f"{stats['conflicts']} row(s) had a column disagreeing with the "
              f"deciding column -- grep 'disagrees' in the output")

    # one spkid_m, one entity. Two rows resolving to the same body is a
    # referential integrity failure, so the loser goes to the unresolved
    # file rather than sitting in the output as a duplicate.
    if args.no_integrity:
        print("\nintegrity check skipped (--no-integrity)")
    else:
        try:
            integrity_check.run(hit_p)
        except integrity_check.IntegrityError as e:
            print(f"integrity check skipped: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())