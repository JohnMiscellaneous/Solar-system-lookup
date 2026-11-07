#!/usr/bin/env python3
"""
Rebuild master_lookup.csv and master_lookup.db from the split pieces.

    python rebuild_master_lookup.py

  1. master_lookup_part01.csv .. partNN.csv  (split_master_lookup.py)
         -> master_lookup.csv
     Pieces are joined in numeric order. Every piece must carry the same
     header, and the numbering must run 1..N with no gaps -- a missing
     piece is an error, not a shorter file.

     If there are no pieces, step 1 is skipped and the existing
     master_lookup.csv is used as-is. That is the "rebuild the .db
     without re-running the merge" case.

  2. master_lookup.csv -> master_lookup.db
     Same table and indexes build_master_lookup.py makes. Rows are
     streamed from the CSV, not loaded whole.

     KEEP INDEXED AND write_db IN SYNC WITH build_master_lookup.py. They
     are copied here so this script has no dependencies.

Standard library only. Works on the CSVs in the folder it lives in.
"""

import csv
import glob
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

OUT_CSV = "master_lookup.csv"
OUT_DB = "master_lookup.db"

# columns lookup.py searches on; indexed NOCASE so a case-insensitive
# equality still uses the index instead of scanning
INDEXED = ["spkid_m", "spkid_f", "spkid", "naif_id", "name",
           "provisional_designation", "cometary_number", "parent_name",
           "parent_number", "parent_provisional_designation",
           "moon_roman_numeral", "moon_greek_letter",
           "moon_des_year", "moon_des_year_number", "alias_of"]

stem, ext = os.path.splitext(OUT_CSV)
PART_RE = re.compile(re.escape(stem) + r"_part(\d+)" + re.escape(ext) + r"$")

csv.field_size_limit(10_000_000)


def find_parts():
    found = {}
    for p in glob.glob(f"{stem}_part*{ext}"):
        m = PART_RE.match(os.path.basename(p))
        if m:
            found[int(m.group(1))] = p
    if not found:
        return []
    want = set(range(1, max(found) + 1))
    missing = sorted(want - set(found))
    if missing:
        sys.exit(f"piece(s) missing: {', '.join(map(str, missing))} "
                 f"-- refusing to build a short {OUT_CSV}")
    return [found[i] for i in sorted(found)]


def join(parts):
    header, total = None, 0
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        for p in parts:
            with open(p, newline="", encoding="utf-8") as f:
                rd = csv.reader(f)
                h = next(rd, None)
                if h is None:
                    sys.exit(f"{p} is empty, not even a header")
                if header is None:
                    header = h
                    w.writerow(header)
                elif h != header:
                    sys.exit(f"{p} header differs from the first piece's")
                n = 0
                for row in rd:
                    w.writerow(row)
                    n += 1
            total += n
            print(f"  {n:>9,} rows <- {os.path.basename(p)}")
    print(f"joined {len(parts)} piece(s), {total:,} rows -> {OUT_CSV}")


def write_db(cols, rows):
    """Copy of build_master_lookup.write_db: one TEXT table, NOCASE
    indexes on the lookup columns. rows may be any iterable of dicts."""
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    db = sqlite3.connect(OUT_DB)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    decl = ", ".join(f'"{c}" TEXT' for c in cols)
    db.execute(f"CREATE TABLE body (_i INTEGER PRIMARY KEY, {decl})")
    ph = ",".join("?" * (len(cols) + 1))
    db.executemany(f"INSERT INTO body VALUES ({ph})",
                   ((i, *(r.get(c, "") for c in cols))
                    for i, r in enumerate(rows)))
    for c in INDEXED:
        if c in cols:
            db.execute(f'CREATE INDEX "ix_{c}" ON body("{c}" COLLATE NOCASE)')
    db.commit()
    db.execute("ANALYZE")
    db.commit()
    db.close()


def build_db():
    if not os.path.exists(OUT_CSV):
        sys.exit(f"{OUT_CSV} not found and no pieces to build it from")
    f = open(OUT_CSV, newline="", encoding="utf-8")
    rd = csv.DictReader(f)
    cols = list(rd.fieldnames or [])
    if not cols:
        sys.exit(f"{OUT_CSV} has no header")
    count = [0]

    def rows():
        for r in rd:
            count[0] += 1
            yield r

    try:
        write_db(cols, rows())
    finally:
        f.close()
    print(f"built {OUT_DB} from {count[0]:,} rows, {len(cols)} columns")


def main():
    parts = find_parts()
    if parts:
        join(parts)
    else:
        print(f"no {stem}_partNN{ext} pieces; using existing {OUT_CSV}")
    build_db()


if __name__ == "__main__":
    main()