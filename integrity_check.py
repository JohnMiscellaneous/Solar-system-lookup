#!/usr/bin/env python3
"""
integrity_check -- one spkid_m, one entity.

spkid_by_column assigns each row independently, so nothing stops two rows
resolving to the SAME body. That is a referential integrity failure: the
master lookup treats spkid_m as the identity of a body, and a table with
it twice cannot be joined on.

This runs over the pair of files spkid_by_column_cli.py writes and moves
every loser of a collision into the unresolved file with its spkid_m
cleared, so the resolved file holds at most one row per spkid_m.

    python integrity_check.py raw_table_resolved.csv
    python integrity_check.py raw_table_resolved.csv --dry-run

WHICH ROW KEEPS THE ID
  1. Earlier wins. File order is the tiebreak, so a rerun is stable and
     the first mention of a body is the one that keeps it.
  2. UNLESS the earlier one was assigned through an alias. An alias is a
     redirect -- an old designation, a lost-and-rediscovered name -- so a
     row that matched the body directly is stronger evidence than one
     that arrived via a pointer, whatever the order. A direct match takes
     the id from an earlier alias-assigned row.
  3. If every candidate came via an alias, rule 1 applies among them.

  spkid_by_column records this: resolved_by ends with ", via alias" when
  the answer came only through a redirect.

THE LOSERS ARE NOT DELETED
  Each moves to the unresolved file with resolved_spkid_m blanked and a
  note naming the row that kept it. Nothing is thrown away -- a collision
  usually means two rows really are the same body and want merging, or one
  of them was matched on something too weak.
"""

import csv
import os
import sys

ID_COL = "resolved_spkid_m"
BY_COL = "resolved_by"
NOTE_COL = "resolve_notes"
ALIAS_MARK = "via alias"


class IntegrityError(Exception):
    """Bad input. Raised, never exited."""


def unresolved_path(resolved_path):
    """raw_resolved.csv -> raw_unresolved.csv, beside it."""
    d, base = os.path.split(resolved_path)
    if "_resolved" not in base:
        raise IntegrityError(
            f"{base} does not look like a *_resolved.csv from "
            f"spkid_by_column")
    return os.path.join(d, base.replace("_resolved", "_unresolved", 1))


def read(path):
    if not os.path.exists(path):
        raise IntegrityError(f"{path} not found")
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        return (rd.fieldnames or []), list(rd)


def via_alias(row):
    return ALIAS_MARK in (row.get(BY_COL) or "")


def label(row):
    for c in ("name", "prov. des.", "provisional_designation", "number",
              "Object", "cometary_number"):
        if (row.get(c) or "").strip():
            return row[c].strip()
    return "?"


def check(rows):
    """-> (keepers, losers). Losers have their id cleared and a note."""
    groups = {}
    for i, r in enumerate(rows):
        v = (r.get(ID_COL) or "").strip()
        if v:
            groups.setdefault(v, []).append(i)

    losers = set()
    for spkid_m, idxs in groups.items():
        if len(idxs) < 2:
            continue
        # a direct match outranks an alias one; otherwise earliest wins
        winner = next((i for i in idxs if not via_alias(rows[i])), idxs[0])
        for i in idxs:
            if i == winner:
                continue
            note = (f"spkid_m {spkid_m} already taken by row "
                    f"{winner + 2} ({label(rows[winner])}"
                    + (", direct match" if via_alias(rows[i])
                       and not via_alias(rows[winner]) else ", earlier")
                    + ")")
            rows[i][NOTE_COL] = "; ".join(
                x for x in [(rows[i].get(NOTE_COL) or "").strip(), note] if x)
            rows[i][ID_COL] = ""
            losers.add(i)

    keepers = [r for i, r in enumerate(rows) if i not in losers]
    return keepers, [rows[i] for i in sorted(losers)]


def run(resolved_path, dry_run=False, quiet=False):
    """-> stats dict. Rewrites both files unless dry_run."""
    unres_path = unresolved_path(resolved_path)
    cols, rows = read(resolved_path)
    if ID_COL not in cols:
        raise IntegrityError(f"{resolved_path} has no {ID_COL} column")
    ucols, urows = read(unres_path)

    keepers, losers = check(rows)
    ids = {(r.get(ID_COL) or "").strip() for r in keepers}
    ids.discard("")

    if not dry_run and losers:
        with open(resolved_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(keepers)
        with open(unres_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ucols or cols,
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(urows + losers)

    stats = {"rows": len(rows), "kept": len(keepers), "moved": len(losers),
             "distinct spkid_m": len(ids)}
    if not quiet:
        print(f"\nintegrity check on {os.path.basename(resolved_path)}")
        for k, v in stats.items():
            print(f"  {k:20} {v}")
        for r in losers[:10]:
            print(f"      moved {label(r)!r}: {r.get(NOTE_COL, '')}")
        if len(losers) > 10:
            print(f"      ... and {len(losers) - 10} more")
        if losers and dry_run:
            print("  dry run: neither file was rewritten")
        if not losers:
            print("  no two rows share an spkid_m")
    return stats


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: integrity_check.py <file>_resolved.csv [--dry-run]",
              file=sys.stderr)
        return 2
    try:
        run(args[0], dry_run="--dry-run" in argv)
    except IntegrityError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())