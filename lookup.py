#!/usr/bin/env python3
"""
lookup -- identifier resolution over the master lookup. LIBRARY ONLY.

No printing, no sys.exit, no argument parsing: import it and call it.
lookup_cli.py is the interactive front end, spkid_by_column.py the batch
one, and neither is needed to use this.

    import lookup
    store = lookup.open_store()            # .db if present, else the .csv
    guesses, idx = lookup.resolve("Saturn VI", store)
    for i in idx:
        print(store[i]["spkid_m"])

Failures raise LookupDataError rather than exiting, so a caller stays in
control of its own process.

TWO BACKENDS, ONE SET OF RULES
  A reading is a set of column conditions, never a Python predicate, so
  the same rule runs as an indexed SQL query or as a scan over parsed CSV
  rows with no second implementation to drift.

      master_lookup.db    indexed. ~0.0003s to open, ~0.0001s a lookup.
      master_lookup.csv   fallback. ~10s to load and ~0.9s a lookup at
                          1.5M rows, because it is a scan.

  build_master_lookup.py writes both; the .db is derived and can be
  deleted and rebuilt at any time. open_store() prefers it silently.

  Matching is case-insensitive. On the SQL side that is COLLATE NOCASE, on
  the CSV side an upper() on both sides. Neither collapses internal
  whitespace, so values are compared as the builder wrote them.

WHAT IT DOES
  classify(q) guesses what KIND of identifier a string is and returns every
  reading it considers, so a wrong answer shows which rule fired. Order is
  the whole trick:

  1  S-designation      S/2009 S 1, S2009_S_1, S/2000 (90) 1,
                        S/1978 P 1 = S/1978 134340 1 = S/1978 1930 BM 1
                        FIRST, so the "2009 S" inside one is never read as
                        a provisional designation. Matched on
                        moon_des_year + moon_des_year_number + the parent,
                        never on the designation string, which has no
                        single correct spelling.
  2  comet num + name   73P/Schwassmann-Wachmann 3
  2b orbit prefix       C/, P/, D/, X/, A/ stripped -- class, not identity
  3  cometary number    50P, 73P-C, 1I, 3D   digits then a type letter, no
                        space, which is what separates it from a
                        provisional designation
  4  roman numeral      Saturn VI, VI Saturn, 22 I, S VI, van Albada I,
                        1992 AX I   (parent by name, planet, planet
                        initial, minor planet number, or designation)
  5  greek letter       Kalliope Beta; Alpha names the primary ITSELF
  6  provisional        1941 SY1, A801 AA, 4109 P-L, 1851 M1, the same
                        closed up (2005CF81, 2009JV18),
                        a short year padded (240 V1 -> 0240 V1), and a
                        fragment (1882 R1-A). BCE years are not supported.
  7  number + name      (22) Kalliope, after bracket cleaning
  8  bare number        tried as minor planet number AND spkid AND naif_id
  9  name               exact, then substring

Ambiguity is never resolved silently -- every reading that matched comes
back, and the caller decides.

ALIAS ROWS
  A row with spkid_m 0 is not a body: it carries an extra designation for
  one that is, named by alias_of. Themisto was found in 1975, lost, and
  rediscovered in 2000, so S/1975 J 1 and S/2000 J 1 are both hers. Every
  match is mapped through alias_of before it is returned, so either
  designation answers Themisto and the alias row itself never surfaces.
"""

import csv
import os
import re
import sqlite3

MASTER_CSV = "master_lookup.csv"
MASTER_DB = "master_lookup.db"
HERE = os.path.dirname(os.path.abspath(__file__))
NUMBERED_BASE = 20_000_000

# columns worth an index; also the only ones a reading may test
INDEXED = ["spkid_m", "spkid_f", "spkid", "naif_id", "name",
           "provisional_designation", "cometary_number", "parent_name",
           "parent_number", "parent_provisional_designation",
           "moon_roman_numeral", "moon_greek_letter",
           "moon_des_year", "moon_des_year_number", "alias_of"]

SHOW = ["spkid_m", "spkid_f", "spkid", "naif_id", "name",
        "provisional_designation", "cometary_number", "parent_name",
        "moon_roman_numeral", "moon_greek_letter", "body_kind"]


class LookupDataError(Exception):
    """Raised instead of exiting, so importers keep control."""


try:
    import roman
except ImportError as exc:                                  # pragma: no cover
    raise LookupDataError("needs the roman package: pip install roman") from exc


GREEK = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta",
         "Theta", "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron",
         "Pi", "Rho", "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega"]
GREEK_UP = {g.upper(): g for g in GREEK}

# Pluto belongs here for PARENT resolution -- its moons carry
# parent_name Pluto and designations written S/1978 P 1 -- even though it
# is a minor planet, not a planet.
PLANETS = ["Mercury", "Venus", "Earth", "Mars",
           "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
PLANET_UP = {p.upper(): p for p in PLANETS}
PLANET_INITIAL = {}
for _p in PLANETS:
    PLANET_INITIAL.setdefault(_p[0].upper(), []).append(_p)

# ---- shapes ---------------------------------------------------------------
# S/2009 S 1, S2009_S_1, S/2000 (90) 1, S/1978 134340 1, S/1978 1930 BM 1
# The parent group is deliberately open: it may be a planet letter, a minor
# planet number of any length, or a whole provisional designation. Capping
# it at two characters lost every Pluto and minor planet form.
S_DESIG = re.compile(r"^S[/_ ]?(\d{4})[_ ]?(.+?)[_ ]?(\d+)$", re.I)
COMET_NUM = re.compile(r"^(\d+)([PCDIXA])(?:-([A-Z]+\d*))?$", re.I)
# 1941 SY1, A801 AA, 4109 P-L -- and the same written closed up, which is
# how several sources store them: 2005CF81, 2009JV18. The space is
# presentation, not identity, so \s* rather than \s+.
# 1941 SY1, A801 AA, 4109 P-L, and the same written closed up (2005CF81),
# plus the two shapes only comets use: a NEGATIVE year for a BCE comet
# (-146 P1) and a fragment suffix (1882 R1-A). Without the fragment group
# "1882 R1-A" was not merely missed, it was read as minor planet 1882
# named "R1-A". P-L and T-n come FIRST in the alternation or the fragment
# group eats the "-L" of a survey designation.
# A YEAR IS FOUR DIGITS. A shorter one is a data entry error, so it is
# zero-padded to match the stored form (240 V1 -> 0240 V1) and the reading
# says so. A BCE year is not supported at all and never matches -- those
# rows are dropped at build time.
PROVISIONAL = re.compile(
    r"^(A\d{3}|\d{1,4})\s*(P-L|T-[123]|[A-Z]{1,2}\d*)"
    r"(?:-([A-Z]+\d*))?$", re.I)
ROMAN = re.compile(r"^[IVXLCDM]+$", re.I)
# 4109P-L closed up is BOTH a survey designation and a valid comet number
# with a fragment (4109P, fragment L). The survey suffixes are a closed
# set, so the ambiguity is detectable and both readings are offered.
SURVEY_CLOSED = re.compile(r"^(\d{4})\s*(P-L|T-[123])$", re.I)
# the prefix is orbit CLASS, not identity, and is deliberately not part of
# spkid_f, so it is stripped and reported rather than matched on
ORBIT_PREFIX = re.compile(r"^([CPDXAI])/(.+)$", re.I)
NUM_SLASH_NAME = re.compile(r"^(\d+[PCDIXA](?:-[A-Z]+\d*)?)/(.+)$", re.I)
BRACKETS = str.maketrans("", "", "()[]{}<>")


def clean(s):
    """Strip brackets and collapse whitespace, so anything pasted straight
    out of a source works: "(22) Kalliope", "S/2000 (90) 1", "[hst5]"."""
    return " ".join((s or "").translate(BRACKETS).split())


def norm(s):
    return " ".join((s or "").split()).upper()


def fkey(s):
    """Designation -> underscore key: whitespace becomes '_'."""
    return "_".join((s or "").split())


# ---- conditions -----------------------------------------------------------
# A reading is OR over alternatives, AND within one. Each condition is
# (op, column, value) with op "eq" or "like". No lambdas: the same
# structure is compiled to SQL or evaluated against parsed rows.

def eq(col, val):
    return ("eq", col, val)


def like(col, val):
    return ("like", col, val)


class Guess:
    def __init__(self, kind, reading, alts, fallback=False):
        self.kind = kind            # which rule fired
        self.reading = reading      # what it means, in words
        self.alts = alts            # [[cond, ...], ...]  OR of ANDs
        self.fallback = fallback    # only reported if nothing better hit
        self.rows = []


# ---- stores ---------------------------------------------------------------
class Store:
    """Rows plus a find(). Subclasses differ only in how they search."""

    def __len__(self):
        raise NotImplementedError

    def __getitem__(self, i):
        raise NotImplementedError

    def find(self, alts):
        raise NotImplementedError

    @property
    def backend(self):
        return type(self).__name__


class DbStore(Store):
    """Indexed SQLite. Opening is instant and lookups are index hits."""

    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        try:
            self.cols = [r[1] for r in
                         self.db.execute("PRAGMA table_info(body)")]
            self.n = self.db.execute("SELECT count(*) FROM body").fetchone()[0]
        except sqlite3.Error as e:
            raise LookupDataError(f"{path} is not a master lookup db: {e}")
        if not self.cols:
            raise LookupDataError(f"{path} has no 'body' table")

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        r = self.db.execute("SELECT * FROM body WHERE _i=?", (i,)).fetchone()
        return dict(r) if r else {}

    def find(self, alts):
        out = set()
        for conds in alts:
            usable = [c for c in conds if c[1] in self.cols]
            if len(usable) != len(conds):
                continue
            where, params = [], []
            for op, col, val in usable:
                if op == "eq":
                    where.append(f'"{col}" = ? COLLATE NOCASE')
                    params.append(val)
                else:
                    where.append(f'"{col}" LIKE ? ESCAPE \'\\\'')
                    params.append("%" + val.replace("\\", "\\\\")
                                  .replace("%", "\\%").replace("_", "\\_") + "%")
            sql = "SELECT _i FROM body WHERE " + " AND ".join(where)
            out |= {r[0] for r in self.db.execute(sql, params)}
        return out


class CsvStore(Store):
    """The whole file parsed into memory. A find() is a scan."""

    def __init__(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            self.rows = list(csv.DictReader(f))
        for i, r in enumerate(self.rows):
            r["_i"] = i
        self.path = path
        self.cols = list(self.rows[0]) if self.rows else []

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]

    def find(self, alts):
        out = set()
        for conds in alts:
            if any(c[1] not in self.cols for c in conds):
                continue
            for r in self.rows:
                for op, col, val in conds:
                    cell = norm(r.get(col))
                    if op == "eq":
                        if cell != norm(val):
                            break
                    elif norm(val) not in cell:
                        break
                else:
                    out.add(r["_i"])
        return out


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def open_store(path=None, prefer_csv=False):
    """A DbStore if a .db is there, else a CsvStore. Pass an explicit path
    to either file to force it."""
    if path:
        if not os.path.exists(path):
            raise LookupDataError(f"{path} not found")
        return CsvStore(path) if path.lower().endswith(".csv") else DbStore(path)
    db = None if prefer_csv else _first_existing(
        os.path.join(HERE, MASTER_DB), MASTER_DB)
    if db:
        return DbStore(db)
    csv_p = _first_existing(os.path.join(HERE, MASTER_CSV), MASTER_CSV)
    if csv_p:
        return CsvStore(csv_p)
    raise LookupDataError(
        f"neither {MASTER_DB} nor {MASTER_CSV} found in {HERE} or "
        f"{os.getcwd()}")


# kept for callers that had a rows list
def load(path=None):
    return open_store(path)


def master_path(path=None):
    return open_store(path).path


# ---- classification -------------------------------------------------------
def parent_tests(tok):
    """A token naming a parent -> [(description, alternatives)]."""
    out = []
    u = norm(tok)

    if u in PLANET_UP:
        out.append((f"parent planet {PLANET_UP[u]}",
                    [[eq("parent_name", PLANET_UP[u])]]))
    elif len(u) == 1 and u in PLANET_INITIAL:
        for p in PLANET_INITIAL[u]:
            out.append((f"parent planet {p} (initial {u})",
                        [[eq("parent_name", p)]]))

    if tok.isdigit():
        out.append((f"parent minor planet {tok}",
                    [[eq("parent_number", tok)]]))

    if not tok.isdigit() and u not in PLANET_UP:
        out.append((f"parent named {tok}", [[eq("parent_name", tok)]]))
        # a parent can be given by its designation rather than its name or
        # number: "1992 AX I" is (5407) 1992 AX I. Numbered-but-unnamed and
        # unnumbered parents have ONLY this, so without it they are
        # unreachable.
        out.append((f"parent designated {tok}",
                    [[eq("parent_provisional_designation", tok)],
                     [eq("parent_provisional_designation", fkey(tok))]]))
    return out


def _with(alts, cond):
    """AND one condition into every alternative."""
    return [list(a) + [cond] for a in alts]


def classify(q):
    """-> list of Guess, most specific rule first."""
    q = clean(q)
    guesses = []
    if not q:
        return guesses

    # 1 -- satellite designation, before anything can read its innards.
    #
    # The stored form is NOT the string. S/YEAR <parent> N spells the
    # parent three ways -- S/1978 P 1, S/1978 134340 1, S/1978 1930 BM 1
    # are all Charon -- so the master keeps only the invariant halves,
    # moon_des_year and moon_des_year_number, and the parent in its own
    # columns. The designation is rebuilt here: year and number matched
    # directly, parent resolved by the same rules any parent reference
    # uses, so all three spellings land on the same row.
    m = S_DESIG.match(q)
    if m:
        year, body, n = m.group(1), norm(m.group(2)), int(m.group(3))
        base = [eq("moon_des_year", year),
                eq("moon_des_year_number", str(n))]
        for desc, alts in parent_tests(m.group(2).strip()):
            guesses.append(Guess(
                "S-designation",
                f"moon #{n} of {year}, of {desc[7:]}",
                [list(a) + base for a in alts]))
        # a master built before the split still carries the string
        key = fkey(f"S{year} {body} {n}")
        slashed = f"S/{year} {body} {n}"
        guesses.append(Guess(
            "S-designation (string)",
            f"the designation as stored text, {slashed}",
            [[eq("spkid_f", key)], [eq("provisional_designation", slashed)]],
            fallback=True))
        return guesses

    # a numbered comet written in full: 73P/Schwassmann-Wachmann 3
    m = NUM_SLASH_NAME.match(q)
    if m:
        num, nm = m.group(1).upper(), m.group(2)
        guesses.append(Guess(
            "comet number + name", f"comet {num} named {nm}",
            [[eq("cometary_number", num), eq("name", nm)]]))
        guesses.append(Guess(
            "cometary number", f"comet {num}, ignoring the name",
            [[eq("cometary_number", num)]], fallback=True))
        return guesses

    # an orbit prefix is class, not identity: strip it, say so, and carry
    # on classifying what is left. C/1984 A1 is stored as 1984 A1.
    m = ORBIT_PREFIX.match(q)
    if m:
        prefix, rest = m.group(1).upper(), m.group(2).strip()
        inner = classify(rest)
        for g in inner:
            g.reading = (f"orbit prefix {prefix}/ stripped (class, not "
                         f"identity) -> {g.reading}")
        return inner

    # 3 -- cometary number: digits then a type letter, no space
    m = COMET_NUM.match(q)
    if m:
        whole, base = q.upper(), m.group(1) + m.group(2).upper()
        guesses.append(Guess(
            "cometary number",
            f"comet {base}" + (f", fragment {m.group(3).upper()}"
                               if m.group(3) else ""),
            [[eq("cometary_number", whole)]]))
        if m.group(3):
            guesses.append(Guess(
                "cometary number (parent)", f"the un-fragmented comet {base}",
                [[eq("cometary_number", base)]]))
        sm = SURVEY_CLOSED.match(q)
        if sm:
            token = f"{sm.group(1)} {sm.group(2).upper()}"
            guesses.append(Guess(
                "provisional designation",
                f"also a survey designation, {token}  ->  {fkey(token)}",
                [[eq("provisional_designation", token)],
                 [eq("spkid_f", fkey(token))]]))
        return guesses

    parts = q.split()

    # 4 / 5 -- a numeral or greek letter at one end, the parent at the
    # other. The parent may be ANY number of words: "van Albada I".
    if len(parts) >= 2:
        for marker, rest in ((parts[-1], " ".join(parts[:-1])),
                             (parts[0], " ".join(parts[1:]))):
            if ROMAN.match(marker):
                try:
                    o = roman.fromRoman(marker.upper())
                except roman.InvalidRomanNumeralError:
                    o = None
                if o is not None:
                    for desc, alts in parent_tests(rest):
                        guesses.append(Guess(
                            "roman numeral",
                            f"moon {marker.upper()} (#{o}) of {desc[7:]}",
                            _with(alts, eq("moon_roman_numeral",
                                           marker.upper()))))
            if norm(marker) in GREEK_UP:
                g = GREEK_UP[norm(marker)]
                if g == "Alpha":
                    # Alpha is the primary itself, so the rest names THIS
                    # row, not a parent.
                    alts = [[eq("moon_greek_letter", "Alpha"),
                             eq("name", rest)]]
                    if rest.isdigit():
                        alts.append([eq("moon_greek_letter", "Alpha"),
                                     eq("spkid",
                                        str(NUMBERED_BASE + int(rest)))])
                    guesses.append(Guess(
                        "greek letter",
                        f"Alpha -- {rest} is itself the primary of a system",
                        alts))
                else:
                    for desc, alts in parent_tests(rest):
                        guesses.append(Guess(
                            "greek letter", f"{g} of {desc[7:]}",
                            _with(alts, eq("moon_greek_letter", g))))
    # no break/return: both ends are tried. "M VI" is two valid roman
    # numerals, so both readings are legitimate -- report both and let the
    # data pick.

    # 6 -- provisional designation, BEFORE number+name, or "1851 M1" reads
    # as "minor planet 1851 named M1"
    if not guesses:
        m = PROVISIONAL.match(q)
        if m:
            year = m.group(1).upper()
            note = []
            if not year.startswith("A") and len(year) != 4:
                year = year.zfill(4)                   # a year is four digits
                note.append("year padded")
            if " " not in q:
                note.append("space restored")
            token = f"{year} {m.group(2).upper()}"
            if m.group(3):
                token += f"-{m.group(3).upper()}"      # fragment is identity
            closed = f"  ({', '.join(note)})" if note else ""
            guesses.append(Guess(
                "provisional designation",
                f"{token}  ->  {fkey(token)}{closed}",
                [[eq("provisional_designation", token)],
                 [eq("spkid_f", fkey(token))]]))

    # 7 -- number + name, which is what "(22) Kalliope" cleans down to
    if not guesses and len(parts) >= 2 and parts[0].isdigit():
        word = " ".join(parts[1:])
        if not ROMAN.match(word) and norm(word) not in GREEK_UP \
                and not re.match(r"^[A-Z]{1,2}\d*$", word, re.I):
            n, spk = parts[0], str(NUMBERED_BASE + int(parts[0]))
            guesses.append(Guess(
                "number + name", f"minor planet ({n}) named {word}",
                [[eq("spkid", spk), eq("name", word)]]))
            guesses.append(Guess(
                "number only", f"minor planet ({n}), ignoring the name",
                [[eq("spkid", spk)]], fallback=True))
            guesses.append(Guess(
                "name only", f"named {word}, ignoring the number",
                [[eq("name", word)]], fallback=True))

    # 8 -- a bare number is several things; report all of them
    if not guesses and q.isdigit() and q != "0":
        # 0 is the reserved alias sentinel, not an identifier for anything
        n = int(q)
        guesses.append(Guess(
            "minor planet number", f"({n}) -> spkid {NUMBERED_BASE + n}",
            [[eq("spkid", str(NUMBERED_BASE + n))]]))
        for col in ("spkid_m", "spkid", "naif_id"):
            guesses.append(Guess(col, f"{col} {n}", [[eq(col, q)]]))

    # 9 -- name, exact then substring
    if not guesses:
        guesses.append(Guess("name", f"name is exactly {q!r}",
                             [[eq("name", q)]]))
        guesses.append(Guess("name (substring)", f"name contains {q!r}",
                             [[like("name", q)]], fallback=True))
        guesses.append(Guess("spkid_f", f"spkid_f is {fkey(q).upper()!r}",
                             [[eq("spkid_f", fkey(q))]], fallback=True))

    # A structured rule firing must not hide the plain-name reading: if
    # "van Albada I" is not a moon designation it may still be a name.
    if guesses and not any(g.kind.startswith("name") for g in guesses):
        guesses.append(Guess("name", f"the whole string as a name, {q!r}",
                             [[eq("name", q)]], fallback=True))
    return guesses


def dedupe(guesses):
    seen, out = set(), []
    for g in guesses:
        k = (g.kind, g.reading)
        if k not in seen:
            seen.add(k)
            out.append(g)
    return out


ALIAS_COL = "alias_of"


def follow_aliases(ids, store):
    """Map alias rows onto the body they point at.

    A body can hold several designations -- Themisto is both S/1975 J 1 and
    S/2000 J 1 -- so the extra ones live on their own rows with spkid_m 0
    and alias_of pointing at the real body. Those rows are never an answer
    in themselves; they are a way of reaching one. A dangling alias_of
    returns nothing rather than the alias row, since that row is not a
    body.

    A DIRECT HIT BEATS AN ALIAS. If a reading matches a real body AND an
    alias row pointing elsewhere, only the direct match survives: an alias
    is a redirect, and a redirect should not compete with the thing that
    was actually found. When every match is an alias, their targets are
    the answer."""
    direct, redirected = set(), set()
    for i in ids:
        target = (store[i].get(ALIAS_COL) or "").strip()
        if target:
            redirected |= store.find([[eq("spkid_m", target)]])
        else:
            direct.add(i)
    # the flag matters downstream: an answer reached only through a
    # redirect is weaker evidence than one found directly, and
    # integrity_check.py uses that to break a tie
    return (direct, False) if direct else (redirected, True)


def resolve(q, store, show_all=False, strict=False):
    """One key -> (readings worth reporting, set of row ids).

    Fallback readings are dropped when something stricter matched.

    strict=True refuses them outright. A substring match is a guess, not an
    identification: "Mercury" matches Freddiemercury, "Venus" matches
    Venusia, "Mars" matches 35 asteroids. That is a reasonable last resort
    when a human is reading the output and can see the reading that fired,
    and it is dangerous in batch assignment, where one substring hit looks
    exactly like an exact hit. Batch callers pass strict=True."""
    guesses = dedupe(classify(q))
    strict_guesses = [g for g in guesses if not g.fallback]
    for g in strict_guesses:
        g.ids = store.find(g.alts)
        g.via_alias = False

    # Only run the fallbacks if nothing stricter hit. They are the
    # expensive ones -- a substring match cannot use an index, so it costs
    # a full scan -- and running them for a query that already resolved is
    # pure waste.
    if strict or any(g.ids for g in strict_guesses):
        guesses = strict_guesses
    else:
        for g in guesses:
            if g.fallback:
                g.ids = store.find(g.alts)

    idx = set()
    for g in guesses:
        g.ids, g.via_alias = follow_aliases(getattr(g, "ids", set()), store)
        g.rows = [store[i] for i in sorted(g.ids)]
        idx |= g.ids
    return guesses, idx


def brief(r):
    """One-line row summary, used by both front ends."""
    return "  ".join(f"{k}={r[k]}" for k in SHOW if r.get(k))