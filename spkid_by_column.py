#!/usr/bin/env python3
"""
spkid_by_column -- give every row of a raw table an spkid_m, using named
columns of that table as identifiers. LIBRARY ONLY; see
spkid_by_column_cli.py for the front end.

    import lookup, spkid_by_column as sbc
    store = lookup.open_store()
    spkid_m, by, notes = sbc.assign_row(raw_row, store,
                                        ["number", "prov. des.", "name"])

PRIMACY, NOT AGREEMENT
  Columns are handed over in the order YOU give them, strongest first. The
  first that resolves decides which body the row is; later columns may only
  narrow that set. A column pointing at a different body is recorded as a
  disagreement and ignored; one pointing nowhere is recorded as unresolved
  and ignored. Neither can empty the answer.

  Put name last. Names collide and drift; a number does not.

  Resolution is STRICT here: the substring-name fallback the interactive
  CLI offers is refused outright. It matches Freddiemercury for "Mercury"
  and 35 asteroids for "Mars", which is a usable hint for a human reading
  the reading that fired and a silent corruption in a batch assignment.
  resolved_by records WHICH reading decided, so an exact name match and a
  number match are distinguishable after the fact.

  A row where several master rows still match after every column is left
  UNASSIGNED, with the count in its notes. Ambiguity is never broken by
  picking the first one.

RAW FILES ARE DIRTY
  read_table normalises bare-CR line endings (some exports use them, and
  the whole file otherwise reads as one row) and scrub() strips Mac Roman
  0xCA padding, zero-width spaces and BOMs before the string reaches the
  classifier.
"""

import csv
import io
import os

import lookup

# invisible padding these exports carry: 0xCA is Mac Roman nbsp
JUNK = "Ê ​﻿�"

# a reasonable default order when the caller does not name columns
DEFAULT_COLUMNS = ["number", "prov. des.", "prov des",
                   "provisional_designation", "spkid_modified", "spkid_f",
                   "spkid_fallback", "spkid", "designation", "name"]


class InputError(Exception):
    """Bad input file or column choice. Raised, never exited."""


def scrub(v):
    """Strip invisible padding, then clean brackets as the classifier does."""
    for ch in JUNK:
        v = (v or "").replace(ch, " ")
    return lookup.clean(v)


def read_table(path):
    """-> (columns, rows). Bare CR counts as a line ending here."""
    if not os.path.exists(path):
        raise InputError(f"{path} not found")
    with open(path, "rb") as f:
        raw = f.read()
    text = (raw.decode("utf-8", "replace")
            .replace("\r\n", "\n").replace("\r", "\n"))
    rd = csv.DictReader(io.StringIO(text))
    rows = [r for r in rd if any((v or "").strip() for v in r.values())]
    return rd.fieldnames or [], rows


def assign_keys(keys, store):
    """[(label, key)] -> (row indices, decided_by, notes), by primacy."""
    answer, by, notes = None, "", []
    for label, key in keys:
        if not key:
            continue
        # strict: a substring name match must never assign an spkid_m.
        # "Mercury" would take Freddiemercury, "Venus" would take Venusia.
        guesses, idx = lookup.resolve(key, store, strict=True)
        g = next((g for g in guesses if g.ids), None)
        how = g.kind if g else ""
        if g is not None and getattr(g, "via_alias", False):
            # reached only through an alias row: still an answer, but
            # weaker than a direct hit, and integrity_check.py breaks ties
            # on exactly this
            how += ", via alias"
        if not idx:
            notes.append(f"{label}={key!r} unresolved")
        elif answer is None:
            answer, by = idx, f"{label}={key!r} [{how}]"
        else:
            both = answer & idx
            if not both:
                notes.append(f"{label}={key!r} disagrees with {by}")
            elif both != answer:
                answer, by = both, f"{by} +{label}={key!r} [{how}]"
    return (answer or set()), by, notes


def assign_row(row, store, columns):
    """One raw row -> (spkid_m, decided_by, notes). '' when not assigned."""
    keys = [(c, scrub(row.get(c))) for c in columns if row.get(c)]
    idx, by, notes = assign_keys(keys, store)
    if not idx:
        return "", by, notes
    if len(idx) > 1:
        notes.append(f"{len(idx)} master rows still match; assigned none")
        return "", by, notes
    return store[next(iter(idx))].get("spkid_m", ""), by, notes


def pick_columns(available, wanted=None):
    """Requested columns that exist, in the requested order.
    Raises rather than quietly resolving nothing."""
    order = wanted or DEFAULT_COLUMNS
    present = [c for c in order if c in available]
    if wanted:
        missing = [c for c in wanted if c not in available]
        if missing:
            raise InputError(
                f"column(s) not in the file: {', '.join(missing)}\n"
                f"available: {', '.join(available)}")
    if not present:
        raise InputError(f"no usable identifier column.\n"
                         f"available: {', '.join(available)}")
    return present


def assign_table(table, store, columns, on_row=None):
    """Annotate every raw row in place. -> stats dict.
    on_row(raw, spkid_m, by, notes) is called per row for progress."""
    stats = dict(rows=len(table), resolved=0, unresolved=0, conflicts=0)
    for r in table:
        spkid_m, by, notes = assign_row(r, store, columns)
        r["resolved_spkid_m"] = spkid_m
        r["resolved_by"] = by
        r["resolve_notes"] = "; ".join(notes)
        stats["resolved" if spkid_m else "unresolved"] += 1
        if any("disagrees" in n for n in notes):
            stats["conflicts"] += 1
        if on_row:
            on_row(r, spkid_m, by, notes)
    return stats


def write_split(path, cols, table, out_dir=None):
    """Write <stem>_resolved.csv and <stem>_unresolved.csv. -> both paths."""
    out_cols = list(cols) + ["resolved_spkid_m", "resolved_by",
                             "resolve_notes"]
    stem = os.path.splitext(os.path.basename(path))[0]
    d = out_dir or os.path.dirname(os.path.abspath(path))
    hit_p = os.path.join(d, f"{stem}_resolved.csv")
    miss_p = os.path.join(d, f"{stem}_unresolved.csv")
    with open(hit_p, "w", newline="", encoding="utf-8") as fo, \
         open(miss_p, "w", newline="", encoding="utf-8") as fu:
        w = csv.DictWriter(fo, fieldnames=out_cols, extrasaction="ignore")
        u = csv.DictWriter(fu, fieldnames=out_cols, extrasaction="ignore")
        w.writeheader()
        u.writeheader()
        for r in table:
            (w if r.get("resolved_spkid_m") else u).writerow(r)
    return hit_p, miss_p