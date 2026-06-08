"""State public-records loaders + owner match (prospect cascade step 7b).

Powers the n8n `RB-PROSPECT-RecordsRefresh` flow: download → parse → normalize
→ upsert into Supabase `public_records`, then per-lead match in the Enrich
cascade (exact state+name → fuzzy jaccard → is_real_person gate).

Sources (build priority FL → CA → CO → OH → AZ; TX has no public-records source):
  - FL_DBPR   : DBPR CILB contractor extract, roofing occupation codes (CCC/RC).
                Quote/comma ASCII; Licensee Name = qualifier (human),
                DBA Name = business entity.
  - FL_SUNBIZ : Sunbiz `cordata` fixed-width `cor` records; up to 6 officers/entity.

Bulk download is environment/credential-gated (DBPR datamart; Sunbiz SFTP with
the public Public/PubAccess1845! creds) and runs on the deploy host. This module
owns parsing + normalization + matching; `fetch_*` functions wrap the transport.

NOTE on field maps: DBPR column indices and Sunbiz byte offsets below come from
the documented layouts (DBPR CILB file layout; Sunbiz cor.html). They are kept as
named constants and MUST be confirmed against the first real extract — adjust the
constant, not the logic. A `--validate` run prints parsed samples for eyeballing.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional

from owner_extractor import _strip_corp_suffix  # reuse the proven suffix stripper

# --------------------------------------------------------------------------- #
# Normalization + person validation (shared with the cascade)
# --------------------------------------------------------------------------- #

_GENERIC_TOKENS = {
    "roofing", "roofs", "roofers", "roof", "exteriors", "exterior", "construction",
    "contractor", "contractors", "services", "service", "company", "co", "group",
    "solutions", "restoration", "remodeling", "solar", "gutters", "siding", "and",
    "the", "of",
}
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")
_PERSON_RE = re.compile(r"^[A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,}\.?$")
_PRIVACY = (
    "whois", "privacy", "redacted", "proxy", "registration private", "domains by proxy",
    "withheld", "data protected", "not available", "n/a", "none", "unknown",
)
_NAME_STOPWORDS = {
    "principal", "contact", "customer", "business", "management", "license", "owner",
    "founder", "president", "manager", "member", "agent", "registered", "qualifier",
    "roofing", "roof", "construction", "contractor", "services", "company", "llc",
    "inc", "corp", "co",
}


def normalize_biz_name(name: str, *, drop_generic: bool = False) -> str:
    """Lower, strip punctuation + entity suffixes, collapse whitespace.

    drop_generic=True additionally removes generic roofing tokens — used only for
    the fuzzy match pass so 'Acme Roofing LLC' and 'Acme Construction' can align.
    """
    if not name:
        return ""
    s = _strip_corp_suffix(name)
    s = _PUNCT.sub(" ", s).lower()
    toks = [t for t in _WS.sub(" ", s).split() if t]
    if drop_generic:
        toks = [t for t in toks if t not in _GENERIC_TOKENS]
    return " ".join(toks).strip()


def _titlecase_person(raw: str) -> str:
    raw = _WS.sub(" ", (raw or "").strip(" ,.")).strip()
    if not raw:
        return ""
    # ALL CAPS public-records names → Title Case; leave mixed case alone.
    if raw.isupper() or raw.islower():
        return " ".join(p.capitalize() for p in raw.split())
    return raw


def normalize_lastfirst(raw: str) -> str:
    """DBPR licensee names arrive 'SMITH, JOHN DOE' (last, first[ middle]).
    Return 'John Doe' (first last) — drop the comma-prefixed surname to the end.
    """
    if not raw:
        return ""
    raw = raw.strip()
    if "," in raw:
        last, rest = raw.split(",", 1)
        rest = rest.strip()
        # rest may be "JOHN DOE" (first middle) — keep first + last only
        first = rest.split()[0] if rest.split() else ""
        return _titlecase_person(f"{first} {last}".strip())
    return _titlecase_person(raw)


def is_real_person(name: str) -> bool:
    """Two-token capitalized human name, not a stopword or privacy-proxy string.
    Gates every write into public_records / Close (cascade step 8)."""
    if not name:
        return False
    n = _WS.sub(" ", name).strip().strip(",.-—–|:;'·•")
    low = n.lower()
    if any(p in low for p in _PRIVACY):
        return False
    if not _PERSON_RE.match(n):
        return False
    parts = [p.lower().strip(".'") for p in n.split() if p]
    if len(parts) < 2:
        return False
    if parts[0] in _NAME_STOPWORDS or parts[-1] in _NAME_STOPWORDS:
        return False
    return True


# --------------------------------------------------------------------------- #
# Record model
# --------------------------------------------------------------------------- #

@dataclass
class PublicRecord:
    source: str            # FL_DBPR | FL_SUNBIZ | CA_CSLB | ...
    state: str
    biz_name_raw: str
    biz_name_norm: str
    owner_name: str
    owner_title: Optional[str] = None
    city: Optional[str] = None
    license_no: Optional[str] = None
    source_row: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# FL DBPR — CILB contractor extract (roofing occupation codes)
# --------------------------------------------------------------------------- #
# Roofing occupation codes in the CILB file: 0611 = Certified Roofing (CCC),
# 0612 = Registered Roofing (RC). Confirm column map against the real extract.
DBPR_ROOFING_OCC_CODES = {"0611", "0612"}
DBPR_COLS = {           # logical field -> 0-based column index (CONFIRM on first file)
    "occupation_code": 0,
    "licensee_name": 2,  # qualifier individual, "LAST, FIRST MIDDLE"
    "dba_name": 3,       # business entity
    "city": 8,
    "state": 9,
    "license_no": 1,
}


def parse_dbpr(text: str, cols: dict | None = None) -> list[PublicRecord]:
    """Parse a DBPR CILB quote/comma extract into roofing PublicRecords.

    Header-aware: if the first row contains known header labels we map by name;
    otherwise we fall back to positional DBPR_COLS.
    """
    cols = cols or DBPR_COLS
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    # Header detection: map by fuzzy header name when present.
    header = [h.strip().lower() for h in rows[0]]
    name_idx = None
    if any("licens" in h or "name" in h or "occupation" in h for h in header):
        def find(*subs):
            for i, h in enumerate(header):
                if any(s in h for s in subs):
                    return i
            return None
        name_idx = {
            "occupation_code": find("occupation"),
            "licensee_name": find("licensee name", "qualifier", "primary name"),
            "dba_name": find("dba", "business name", "doing business"),
            "city": find("city"),
            "state": find("state"),
            "license_no": find("license number", "license no", "lic num"),
        }
        body = rows[1:]
    else:
        body = rows

    idx = name_idx or cols
    out: list[PublicRecord] = []
    for r in body:
        def g(key):
            i = idx.get(key)
            return r[i].strip() if i is not None and i < len(r) else ""

        occ = g("occupation_code")
        if DBPR_ROOFING_OCC_CODES and occ and occ not in DBPR_ROOFING_OCC_CODES:
            continue
        owner = normalize_lastfirst(g("licensee_name"))
        if not is_real_person(owner):
            continue
        biz = g("dba_name") or ""
        if not biz:
            continue
        out.append(PublicRecord(
            source="FL_DBPR",
            state=(g("state") or "FL").upper()[:2],
            biz_name_raw=biz,
            biz_name_norm=normalize_biz_name(biz),
            owner_name=owner,
            owner_title="Qualifier",
            city=_titlecase_person(g("city")) or None,
            license_no=g("license_no") or None,
            source_row={"occupation_code": occ},
        ))
    return out


# --------------------------------------------------------------------------- #
# FL Sunbiz — cordata fixed-width `cor` records (up to 6 officers)
# Byte offsets (1-based) reverse-engineered from real cordata records (1440-byte
# records; the published cor.html layout was inaccurate). Verified against the
# daily SFTP file: officer slots are 128 bytes, 6 of them, starting at 669, with
# SEPARATE last/first name fields (not a single name field).
#   TRUESPAN ROOFING:  AMBRP | JIMENEZ            | OSVALDO   -> Osvaldo Jimenez
#   EASY GAME (3 offs): MGR P | BRANT/FORD/SPAIN  | KRISTOPHER/...  @ 669/797/925
SUNBIZ = {
    "name": (13, 204),               # corporate/entity name — confirmed
    "officer_block_start": 669,
    "officer_slot_len": 128,
    "officer_count": 6,
    # within a slot (1-based relative):
    "off_title_rel": (1, 5),         # title code (e.g. "MGR P", "AMBRP")
    "off_last_rel": (6, 25),         # surname
    "off_first_rel": (26, 45),       # given name(s)
}
# Title-code fragments that mark a principal/owner (substring match).
SUNBIZ_PRINCIPAL_TITLES = {"P", "PRES", "MGR", "AMBR", "MBR", "CEO", "OWN"}


def _slice(line: str, start: int, end: int) -> str:
    return line[start - 1:end].strip()


def normalize_officer_name(raw: str) -> str:
    """Sunbiz officer names are often 'LAST FIRST MIDDLE' or 'LAST, FIRST'."""
    raw = _WS.sub(" ", (raw or "").strip())
    if not raw:
        return ""
    if "," in raw:
        return normalize_lastfirst(raw)
    parts = raw.split()
    if len(parts) >= 2:
        # assume LAST FIRST -> First Last
        return _titlecase_person(f"{parts[1]} {parts[0]}")
    return _titlecase_person(raw)


# Bulk cordata is millions of entities; for prospecting we only want the
# industry. Cheap substring pre-filter on the raw name region rejects ~99%
# before the (more expensive) officer parse.
ROOFING_NAME_RE = re.compile(r"roof", re.I)


def iter_sunbiz_records(lines: Iterable[str], name_filter: "re.Pattern | None" = None):
    """Stream cordata `cor` records → PublicRecord. Generator (memory-safe for
    the 1.7GB quarterly). name_filter (e.g. ROOFING_NAME_RE) is matched against
    the raw entity name and short-circuits before officer parsing."""
    base = SUNBIZ["officer_block_start"]
    slot = SUNBIZ["officer_slot_len"]
    for line in lines:
        if len(line) < base:
            continue
        biz = _slice(line, *SUNBIZ["name"])
        if not biz:
            continue
        if name_filter and not name_filter.search(biz):
            continue
        chosen = None
        for n in range(SUNBIZ["officer_count"]):
            s0 = base + n * slot - 1  # 0-based slot start (block start is 1-based)
            title = line[s0 + SUNBIZ["off_title_rel"][0] - 1: s0 + SUNBIZ["off_title_rel"][1]].strip()
            last = line[s0 + SUNBIZ["off_last_rel"][0] - 1: s0 + SUNBIZ["off_last_rel"][1]].strip()
            first = line[s0 + SUNBIZ["off_first_rel"][0] - 1: s0 + SUNBIZ["off_first_rel"][1]].strip()
            if not last and not first:
                continue
            owner = _titlecase_person(f"{first} {last}".strip())
            if not is_real_person(owner):
                continue
            if chosen is None:                       # first human = fallback
                chosen = (owner, title)
            tu = title.upper()
            if any(code in tu for code in SUNBIZ_PRINCIPAL_TITLES):
                chosen = (owner, title)              # prefer a principal
                break
        if not chosen:
            continue
        yield PublicRecord(
            source="FL_SUNBIZ",
            state="FL",
            biz_name_raw=biz,
            biz_name_norm=normalize_biz_name(biz),
            owner_name=chosen[0],
            owner_title=chosen[1] or None,
            source_row={},
        )


def parse_sunbiz_cor(lines: Iterable[str], name_filter=None) -> list[PublicRecord]:
    return list(iter_sunbiz_records(lines, name_filter))


# --------------------------------------------------------------------------- #
# Match (cascade step 7b)
# --------------------------------------------------------------------------- #

FetchFn = Callable[[str, str], list[dict]]
# FetchFn(state, biz_name_norm) -> candidate rows from public_records.


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def match_public_record(
    state: str,
    biz_name: str,
    fetch_exact: FetchFn,
    fetch_state: FetchFn | None = None,
    city: str | None = None,
    fuzzy_floor: float = 0.6,
) -> Optional[dict]:
    """Return {owner_name, owner_source, owner_confidence, ...} or None.

    1. exact state + biz_name_norm  → confidence 0.95 (prefer FL_DBPR/CA_CSLB).
    2. else fuzzy jaccard ≥ floor on generic-stripped names within state.
    """
    if not state or not biz_name:
        return None
    state = state.upper()[:2]
    norm = normalize_biz_name(biz_name)
    if not norm:
        return None

    exact = fetch_exact(state, norm) or []
    if exact:
        best = sorted(exact, key=lambda r: 0 if r.get("source") in ("FL_DBPR", "CA_CSLB") else 1)[0]
        if is_real_person(best.get("owner_name", "")):
            return {**best, "owner_confidence": "exact", "match_score": 0.95}

    if fetch_state is None:
        return None
    target = normalize_biz_name(biz_name, drop_generic=True)
    best_row, best_score = None, 0.0
    for r in fetch_state(state, "") or []:
        cand = normalize_biz_name(r.get("biz_name_raw", ""), drop_generic=True)
        sc = _jaccard(target, cand)
        if city and r.get("city") and city.strip().lower() == str(r["city"]).strip().lower():
            sc += 0.05
        if sc > best_score:
            best_row, best_score = r, sc
    if best_row and best_score >= fuzzy_floor and is_real_person(best_row.get("owner_name", "")):
        return {**best_row, "owner_confidence": "fuzzy", "match_score": round(best_score, 3)}
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv: list[str]) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="FL public-records loader")
    ap.add_argument("source", choices=["fl_dbpr", "fl_sunbiz"])
    ap.add_argument("path", help="local extract file (csv for dbpr, cor text for sunbiz)")
    ap.add_argument("--validate", action="store_true", help="print first 10 parsed rows, no upsert")
    ap.add_argument("--upsert", action="store_true", help="upsert into Supabase public_records")
    args = ap.parse_args(argv)

    raw = open(args.path, "r", encoding="latin-1", errors="replace").read()
    if args.source == "fl_dbpr":
        recs = parse_dbpr(raw)
    else:
        recs = parse_sunbiz_cor(raw.splitlines())

    print(f"parsed {len(recs)} {args.source} records with a valid human owner")
    if args.validate:
        for r in recs[:10]:
            print(f"  {r.owner_name:24} ({r.owner_title or '-':>9})  <-  {r.biz_name_raw[:48]}")
        return 0
    if args.upsert:
        from supabase_client import upsert_public_records
        n = upsert_public_records([r.to_row() for r in recs])
        print(f"upserted {n} rows")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
