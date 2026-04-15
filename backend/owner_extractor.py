"""
Owner-name extraction with two strategies tried in order:

1. From the prospect's own website HTML (free — the HTML is already fetched
   during signal detection, so this is a pure parse, no extra network).
2. BBB fallback — search bbb.org for the business, follow the top result,
   pull the Principal Contacts block.

Both are best-effort. On failure we return ``("", "none")`` so the pipeline
can treat the field as optional.
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Optional
from urllib.parse import quote_plus

import requests


# --- Common patterns -----------------------------------------------------

# Keywords that identify someone as the owner in plain text
OWNER_HINTS = (
    "owner", "founder", "co-founder", "president", "ceo",
    "proprietor", "principal", "operator",
)

# A reasonable "looks like a person's name" regex. We require:
#   - capitalized first name of 2+ letters
#   - optional middle initial
#   - capitalized last name of 2+ letters
#   - allow hyphens/apostrophes (O'Brien, Mary-Jane)
NAME_RE = re.compile(
    r"\b([A-Z][a-z'’\-]{1,})(?:\s+[A-Z]\.?)?\s+([A-Z][a-z'’\-]{1,})\b"
)

# Strip HTML tags for plain-text scanning
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return unescape(TAG_RE.sub(" ", html))


def _pick_first_name(text: str) -> Optional[str]:
    m = NAME_RE.search(text)
    if not m:
        return None
    name = m.group(0).strip()
    # Reject obvious non-names (single-word "Our Team", etc. are excluded by regex)
    # but also reject common false positives where the first word is a month,
    # weekday, or title word.
    bad_first = {
        "Our", "The", "About", "Contact", "Meet", "Read", "View", "More",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
    }
    first_word = name.split()[0]
    if first_word in bad_first:
        return None
    return name


# --- Strategy 1: the prospect's own site --------------------------------

def extract_owner_from_html(html: str) -> Optional[str]:
    """Pull the owner's name out of a business's own website, if present."""
    if not html:
        return None

    # 1a. Schema.org JSON-LD — most reliable when present.
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for node in _iter_json_nodes(data):
            if not isinstance(node, dict):
                continue
            for key in ("founder", "owner", "ceo", "president"):
                val = node.get(key)
                if isinstance(val, dict) and val.get("name"):
                    return str(val["name"]).strip()
                if isinstance(val, str) and val.strip():
                    return val.strip()

    text = _strip_html(html)

    # 1b. "Owner: Jane Smith" / "Founded by Jane Smith" / "Meet Jane, owner"
    patterns = [
        r"(?:owner|founder|co-?founder|president|ceo|proprietor|principal)\s*[:\-—]\s*([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})",
        r"(?:founded|owned|operated|established)\s+by\s+([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})",
        r"Meet\s+([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})\s*,?\s*(?:our\s+)?(?:owner|founder|president|ceo)",
        r"([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})\s*,\s*(?:owner|founder|president|ceo|proprietor|principal)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # Re-validate via the strict name regex
            if NAME_RE.fullmatch(candidate):
                return candidate

    return None


def _iter_json_nodes(node):
    """Walk a JSON blob yielding every dict/list we find."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_json_nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_json_nodes(v)


# --- Strategy 2: BBB fallback -------------------------------------------

BBB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def find_owner_on_bbb(
    business_name: str,
    city: str = "",
    state: str = "",
    timeout: float = 6.0,
) -> Optional[str]:
    """Search BBB for a business, follow the first result, extract the Principal."""
    if not business_name:
        return None

    loc = ", ".join(p for p in (city, state) if p).strip(", ")
    search_url = (
        "https://www.bbb.org/search?find_text="
        + quote_plus(business_name)
        + (f"&find_loc={quote_plus(loc)}" if loc else "")
    )

    try:
        r = requests.get(search_url, headers=BBB_HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return None
    except requests.RequestException:
        return None

    profile_url = _first_bbb_profile_url(r.text)
    if not profile_url:
        return None

    try:
        p = requests.get(profile_url, headers=BBB_HEADERS, timeout=timeout)
        if p.status_code != 200:
            return None
    except requests.RequestException:
        return None

    return _extract_bbb_principal(p.text)


def _first_bbb_profile_url(search_html: str) -> Optional[str]:
    # BBB profile URLs look like: /us/ca/san-diego/profile/roofing-contractors/acme-0000-00000
    m = re.search(
        r'href="(/us/[a-z]{2}/[^"/]+/profile/[a-z0-9\-]+/[^"]+)"',
        search_html,
    )
    if not m:
        return None
    return "https://www.bbb.org" + m.group(1)


def _extract_bbb_principal(profile_html: str) -> Optional[str]:
    """BBB profile pages list principals inside a 'Business Management' section.

    Historically the block is structured roughly as:
        <dt>Mr. Jane Smith</dt><dd>Owner</dd>
    We try both a dt/dd pattern and a looser 'Principal Contacts' regex.
    """
    if not profile_html:
        return None

    # Pattern A: tight dt/dd pairs in management section
    for m in re.finditer(
        r"<d[td][^>]*>\s*(?:Mr\.|Ms\.|Mrs\.|Dr\.)?\s*([A-Z][A-Za-z'’\-]+(?:\s+[A-Z]\.?)?\s+[A-Z][A-Za-z'’\-]+)\s*</d[td]>\s*<d[td][^>]*>\s*([^<]{3,40})\s*</d[td]>",
        profile_html,
    ):
        name = m.group(1).strip()
        role = m.group(2).strip().lower()
        if any(h in role for h in OWNER_HINTS):
            return name

    # Pattern B: loose scan of the Principal Contacts section text
    section = re.search(
        r"Principal\s+Contacts.*?(?=Customer\s+Contacts|Additional\s+Contact|</section>|$)",
        profile_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if section:
        text = _strip_html(section.group(0))
        for m in NAME_RE.finditer(text):
            # Window a few words around the name to check for an owner hint
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            window = text[start:end].lower()
            if any(h in window for h in OWNER_HINTS):
                return m.group(0).strip()

    # Pattern C: last-ditch — first name near the phrase "Business Management"
    biz_mgmt = re.search(
        r"Business\s+Management.{0,2000}",
        profile_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if biz_mgmt:
        text = _strip_html(biz_mgmt.group(0))
        cand = _pick_first_name(text)
        if cand:
            return cand

    return None


# --- Pipeline entry point ------------------------------------------------

def resolve_owner(
    html: str,
    business_name: str,
    city: str = "",
    state: str = "",
    allow_bbb: bool = True,
) -> tuple[str, str]:
    """Return (owner_name, source).

    source is one of: "website", "bbb", "none".
    """
    name = extract_owner_from_html(html)
    if name:
        return name, "website"
    if allow_bbb:
        name = find_owner_on_bbb(business_name, city, state)
        if name:
            return name, "bbb"
    return "", "none"
