"""
Owner-name extraction with a free multi-source fallback chain.

Order, highest precision first:
  1. BBB search + profile (multiple query strategies, profile verified by
     the CSV's domain or phone before we trust the name).
  2. Business website — homepage HTML already fetched by the pipeline, plus
     /about, /team, /contact subpages; owner extracted only when it appears
     near an owner/founder/president keyword.
  3. OpenCorporates — state business registry aggregator. Free public
     search returns registered officers.

Every strategy returns `("", "none")` on failure so the pipeline can treat
the field as optional. Source is tagged on the hit ("bbb", "website",
"opencorporates") so you can weight them differently downstream.
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import requests


# Keywords that identify someone as the owner in plain text
OWNER_HINTS = (
    "owner", "founder", "co-founder", "president", "ceo",
    "proprietor", "principal", "operator",
)

# Words that look like names to the regex but are really BBB section labels,
# titles, or generic filler. Any candidate whose first OR last word is on this
# list is rejected — otherwise we end up writing "Principal Contacts" or
# "Customer Contacts" into the owner column.
NAME_STOPWORDS = {
    "principal", "principals", "contact", "contacts", "customer", "customers",
    "business", "management", "additional", "information", "email",
    "address", "addresses", "social", "media", "phone", "website", "reviews",
    "complaints", "overview", "licensing", "licenses", "license", "details",
    "accredited", "accreditation", "rating", "report", "reports", "local",
    "service", "services", "products", "years", "mr", "mrs", "ms", "dr",
    # BBB "Business Details" labels that look name-shaped
    "file", "opened", "started", "incorporated", "locally", "since",
    "entity", "type", "other", "home", "page", "overview", "rev",
}

# A reasonable "looks like a person's name" regex. We require:
#   - capitalized first name of 2+ letters
#   - optional middle initial
#   - capitalized last name of 2+ letters
#   - allow hyphens/apostrophes (O'Brien, Mary-Jane)
NAME_RE = re.compile(
    r"\b([A-Z][a-z'’\-]{1,})(?:\s+[A-Z]\.?)?\s+([A-Z][a-z'’\-]{1,})\b"
)

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return unescape(TAG_RE.sub(" ", html))


def _is_plausible_name(name: str) -> bool:
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        return False
    first, last = parts[0], parts[-1]
    if first.lower() in NAME_STOPWORDS or last.lower() in NAME_STOPWORDS:
        return False
    return True


def _pick_first_name(text: str) -> Optional[str]:
    for m in NAME_RE.finditer(text):
        cand = m.group(0).strip()
        if _is_plausible_name(cand):
            return cand
    return None


BBB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_domain(website: str) -> str:
    if not website:
        return ""
    s = website.lower().strip()
    s = re.sub(r"^[a-z]+://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.split("/")[0].split(":")[0].split("?")[0]


_CORP_SUFFIX_RE = re.compile(
    r"[,\s]+(?:l\.?l\.?c\.?|inc\.?|incorporated|co\.?|corp\.?|corporation|"
    r"p\.?c\.?|p\.?a\.?|p\.?l\.?l\.?c\.?|ltd\.?|llp|dba)\b\.?",
    re.IGNORECASE,
)


def _strip_corp_suffix(name: str) -> str:
    """Drop LLC/Inc/Corp-style suffixes so BBB name search is more forgiving."""
    if not name:
        return ""
    cleaned = _CORP_SUFFIX_RE.sub("", name).strip(" ,.")
    return re.sub(r"\s+", " ", cleaned)


def _name_core(name: str) -> str:
    """Drop both corp suffix and industry words like 'Roofing' to maximize
    recall on fuzzy BBB matches (e.g. 'Acme Roofing LLC' -> 'Acme'). Only
    used when every other strategy has failed."""
    if not name:
        return ""
    s = _strip_corp_suffix(name)
    s = re.sub(
        r"\b(?:roofing|exteriors|construction|contractors?|services?|"
        r"company|group|solutions|&|and)\b",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", s).strip(" ,.&-")


def _bbb_search(query: str, loc: str = "", timeout: float = 6.0) -> list[str]:
    """Run one BBB search and return ordered profile URLs."""
    if not query:
        return []
    url = "https://www.bbb.org/search?find_text=" + quote_plus(query)
    if loc:
        url += "&find_loc=" + quote_plus(loc)
    try:
        r = requests.get(url, headers=BBB_HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text:
            return []
    except requests.RequestException:
        return []
    return _bbb_profile_urls(r.text, limit=10)


def find_owner_on_bbb(
    business_name: str,
    city: str = "",
    state: str = "",
    website: str = "",
    phone: str = "",
    timeout: float = 6.0,
    max_candidates: int = 8,
) -> Optional[str]:
    """Try several BBB search strategies in order of precision, verify each
    candidate profile actually references our business, then pull the owner.

    Strategies:
      1. Search BBB with the bare domain — BBB's free-text search accepts
         URLs and frequently returns the exact business, bypassing
         name-mismatch failures ("Joe's Roofing LLC" vs "Joe Roofing Inc").
      2. Search by business name + city/state (tight geographic filter).
      3. Search by business name + state only (some orgs aren't listed in
         their displayed city).
      4. Search by business name alone (last resort; widest net).
    """
    domain_needle = _normalize_domain(website)
    phone_digits = re.sub(r"\D", "", phone or "")
    if len(phone_digits) == 11 and phone_digits.startswith("1"):
        phone_digits = phone_digits[1:]

    loc_full = ", ".join(p for p in (city, state) if p).strip(", ")

    seen_profiles: set[str] = set()
    queries: list[tuple[str, str]] = []

    # 1. Exact domain — highest precision.
    if domain_needle:
        queries.append((domain_needle, ""))
        # Try the root domain without the TLD too — sometimes BBB indexes
        # the brand word alone ("acmeroofing" -> brand search).
        brand = domain_needle.rsplit(".", 1)[0]
        if brand and brand != domain_needle and len(brand) >= 4:
            queries.append((brand, loc_full or state or ""))

    # 2. Phone — unique per business, very high precision if listed.
    if phone_digits and len(phone_digits) == 10:
        pretty = f"({phone_digits[:3]}) {phone_digits[3:6]}-{phone_digits[6:]}"
        queries.append((pretty, ""))
        queries.append((phone_digits, ""))

    # 3. Full business name + full location.
    if business_name and loc_full:
        queries.append((business_name, loc_full))

    # 4. Name without corporate suffix (LLC/Inc/etc.) — catches "Acme Roofing"
    # when BBB has "Acme Roofing LLC" and vice versa.
    stripped = _strip_corp_suffix(business_name) if business_name else ""
    if stripped and stripped.lower() != (business_name or "").lower() and loc_full:
        queries.append((stripped, loc_full))

    # 5. Name + state only (some orgs aren't listed in their displayed city).
    if business_name and state and loc_full != state:
        queries.append((business_name, state))

    # 6. Bare name (widest net, name-only search).
    if business_name:
        queries.append((business_name, ""))

    # 7. Core brand word only ('Acme Roofing LLC' -> 'Acme') with location.
    # Last resort — high recall, low precision; profile verification still
    # has to pass or we don't accept the match.
    core = _name_core(business_name) if business_name else ""
    if core and core.lower() != (business_name or "").lower() and (loc_full or state):
        queries.append((core, loc_full or state))

    checked = 0
    for query, loc in queries:
        for profile_url in _bbb_search(query, loc, timeout=timeout):
            if profile_url in seen_profiles:
                continue
            seen_profiles.add(profile_url)
            checked += 1
            if checked > max_candidates:
                return None

            try:
                p = requests.get(profile_url, headers=BBB_HEADERS, timeout=timeout)
                if p.status_code != 200:
                    continue
            except requests.RequestException:
                continue

            if domain_needle or phone_digits:
                if not _profile_matches(p.text, domain_needle, phone_digits):
                    continue
            name = _extract_bbb_principal(p.text)
            if name:
                return name
    return None


def _profile_matches(profile_html: str, domain: str, phone_digits: str) -> bool:
    """Return True if the BBB profile references our website or phone."""
    if not profile_html or (not domain and not phone_digits):
        return False
    lower_html = profile_html.lower()
    if domain and domain in lower_html:
        return True
    if phone_digits and len(phone_digits) >= 10:
        profile_digits = re.sub(r"\D", "", profile_html)
        if phone_digits in profile_digits:
            return True
    return False


def _bbb_profile_urls(search_html: str, limit: int = 5) -> list[str]:
    """Return up to `limit` unique BBB profile URLs from a search results page,
    preserving order (BBB sorts by relevance, so position 0 is best)."""
    urls: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'href="(/us/[a-z]{2}/[^"/]+/profile/[a-z0-9\-]+/[^"]+)"',
        search_html,
    ):
        path = m.group(1)
        # Strip the /addressId/... suffix for dedup — same business, many addrs.
        key = path.split("/addressId/")[0]
        if key in seen:
            continue
        seen.add(key)
        urls.append("https://www.bbb.org" + path)
        if len(urls) >= limit:
            break
    return urls


def _first_bbb_profile_url(search_html: str) -> Optional[str]:
    urls = _bbb_profile_urls(search_html, limit=1)
    return urls[0] if urls else None


HONORIFIC_RE = re.compile(r"(?:Mr|Mrs|Ms|Miss|Dr|Rev)\.?\s+", re.IGNORECASE)

# Matches "Firstname [M.] Lastname" — used after stripping honorifics.
HUMAN_NAME_RE = re.compile(
    r"\b([A-Z][a-z'’\-]{1,})(?:\s+[A-Z]\.?)?\s+([A-Z][a-z'’\-]{1,})\b"
)


def _clean_name(raw: str) -> Optional[str]:
    """Strip honorifics / trailing role fragments, validate it looks human."""
    if not raw:
        return None
    # Strip leading "Mr./Mrs./Ms./Dr./Rev." (possibly repeated)
    s = raw.strip()
    while True:
        new = HONORIFIC_RE.sub("", s, count=1).strip()
        if new == s:
            break
        s = new
    # Cut at first comma — after comma is usually the role ("John Smith, CEO")
    s = s.split(",")[0].strip()
    m = HUMAN_NAME_RE.search(s)
    if not m:
        return None
    name = m.group(0).strip()
    return name if _is_plausible_name(name) else None


def _extract_bbb_principal(profile_html: str) -> Optional[str]:
    """Parse a BBB business profile for the first principal / manager name.

    BBB's "Business Details" block looks like this in the rendered HTML:
        <dt>Business Management:</dt>
        <dd>Heather A. Reimiller, Manager</dd>
        <dd>Christopher B. Thomas, Qualifying Partner</dd>
        <dd>Doris Marrs, Interim CFO</dd>

    And "Additional Contact Information" similarly contains:
        <dt>Principal Contacts</dt>
        <dd>Christopher B. Thomas, Qualifying Partner</dd>

    We target those specific dt labels and grab the first plausible name
    from the immediately-following dd siblings. Matching "any dt/dd pair"
    picks up unrelated labels like 'BBB File Opened: 12/20/2019', which is
    where the bogus "File Opened" owner names came from.
    """
    if not profile_html:
        return None

    # Ordered by preference: Business Management is usually the fullest list,
    # Principal Contacts is more curated, Customer Contacts is the fallback.
    target_labels = [
        r"Business\s+Management",
        r"Principal\s+Contacts?",
        r"Customer\s+Contacts?",
    ]

    for label in target_labels:
        # Match a <dt> containing exactly this label (optionally with a colon
        # and surrounding whitespace). Capture everything up to the next <dt>
        # or the end of the enclosing <dl>; the captured chunk contains the
        # dd siblings we care about.
        dt_block = re.search(
            r"<dt[^>]*>\s*" + label + r"\s*:?\s*</dt>(.*?)(?=<dt[\s>]|</dl>)",
            profile_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not dt_block:
            continue

        # Pull each <dd>...</dd> value and try to extract a real name.
        for dd in re.finditer(
            r"<dd[^>]*>(.*?)</dd>",
            dt_block.group(1),
            flags=re.IGNORECASE | re.DOTALL,
        ):
            raw = _strip_html(dd.group(1)).strip()
            if not raw:
                continue
            name = _clean_name(raw)
            if name:
                return name

    # Fallback: honorific-prefixed name anywhere in the profile. Low-
    # confidence but catches layouts we haven't seen yet.
    hm = re.search(
        r"(?:Mr|Mrs|Ms|Miss|Dr|Rev)\.?\s+([A-Z][a-z'’\-]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]+)",
        profile_html,
    )
    if hm:
        name = _clean_name(hm.group(1))
        if name:
            return name

    return None


# --- Strategy 2: business website -----------------------------------------

# Strict owner-hint patterns. We only accept a name that appears next to one
# of these keywords — otherwise any capitalized two-word phrase on the
# homepage (staff, testimonial authors, city names) could be mistaken for
# the owner. The hint word must be within a small text window of the name.
OWNER_CONTEXT_PATTERNS = [
    re.compile(
        r"(?:owner|founder|co-?founder|president|ceo|proprietor|principal|"
        r"operator)\s*[:\-\u2014]?\s*(?:is\s+)?"
        r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+)?"
        r"([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:founded|owned|operated|established)\s+by\s+"
        r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+)?"
        r"([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:Meet|Hi,?\s+I['’]m|I\s+am)\s+"
        r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+)?"
        r"([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})"
        r"[\s,]+(?:our\s+)?(?:owner|founder|president|ceo)",
        re.IGNORECASE,
    ),
    re.compile(
        r"((?:Mr|Mrs|Ms|Miss|Dr)\.?\s+)?"
        r"([A-Z][a-z'’\-]{1,}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'’\-]{1,})"
        r"\s*,\s*(?:owner|founder|co-?founder|president|ceo|proprietor|"
        r"principal|operator)\b",
        re.IGNORECASE,
    ),
]

# Schema.org JSON-LD is the single most reliable website signal when present.
def _extract_owner_from_jsonld(html: str) -> Optional[str]:
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, flags=re.IGNORECASE | re.DOTALL,
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
                    cand = _clean_name(str(val["name"]))
                    if cand:
                        return cand
                if isinstance(val, str):
                    cand = _clean_name(val)
                    if cand:
                        return cand
    return None


def _iter_json_nodes(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_json_nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_json_nodes(v)


def extract_owner_from_html(html: str) -> Optional[str]:
    """Return a name from the HTML only when it sits next to an owner-
    hint word. Strict by design — false positives from an unanchored
    name regex would write staff or testimonial names into the owner
    column."""
    if not html:
        return None
    # Schema.org first (most reliable).
    jl = _extract_owner_from_jsonld(html)
    if jl:
        return jl

    text = _strip_html(html)
    for pat in OWNER_CONTEXT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        raw = (m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)) or ""
        cand = _clean_name(raw)
        if cand:
            return cand
    return None


WEBSITE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

OWNER_SUBPAGE_PATHS = (
    "/about", "/about-us", "/about-our-company",
    "/team", "/our-team", "/meet-the-team", "/leadership",
    "/our-story", "/who-we-are",
    "/contact", "/contact-us",
    "/staff", "/management",
)


def scrape_owner_subpages(website: str, timeout: float = 5.0) -> Optional[str]:
    """Try a handful of common owner-revealing subpaths on the business
    website. Stops at the first hit. Cheap because each page is a single
    GET with a small timeout; we don't run the full fetcher."""
    if not website:
        return None
    base = website if website.startswith("http") else "https://" + website
    parsed = urlparse(base)
    if not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path in OWNER_SUBPAGE_PATHS:
        url = urljoin(origin, path)
        try:
            r = requests.get(url, headers=WEBSITE_HEADERS, timeout=timeout)
            if r.status_code != 200 or not r.text:
                continue
        except requests.RequestException:
            continue
        name = extract_owner_from_html(r.text)
        if name:
            return name
    return None


# --- Strategy 3: OpenCorporates -------------------------------------------

# Public API. Unauthenticated requests are rate-limited but fine for small
# batches; paid tiers exist for volume.
OPENCORP_SEARCH = "https://api.opencorporates.com/v0.4/companies/search"
OPENCORP_COMPANY = "https://api.opencorporates.com/v0.4/companies/{juris}/{num}"


def find_owner_on_opencorporates(
    business_name: str,
    state: str = "",
    timeout: float = 8.0,
) -> Optional[str]:
    """Search OpenCorporates by name + jurisdiction; return the first listed
    officer's name that passes our plausibility filter."""
    if not business_name:
        return None
    params = {"q": business_name, "per_page": "5"}
    if state and len(state) == 2:
        params["jurisdiction_code"] = f"us_{state.lower()}"
    try:
        r = requests.get(OPENCORP_SEARCH, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None

    try:
        companies = payload["results"]["companies"]
    except (KeyError, TypeError):
        return None

    for wrap in companies:
        comp = wrap.get("company") or {}
        juris = comp.get("jurisdiction_code")
        num = comp.get("company_number")
        if not juris or not num:
            continue
        name = _fetch_opencorp_officer(juris, num, timeout)
        if name:
            return name
    return None


def _fetch_opencorp_officer(juris: str, num: str, timeout: float) -> Optional[str]:
    """Fetch a company detail record and return the first plausible
    officer name. OpenCorporates wraps officers under
    data.company.officers[*].officer.name."""
    url = OPENCORP_COMPANY.format(juris=juris, num=num)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        payload = r.json()
    except (requests.RequestException, ValueError):
        return None

    try:
        officers = payload["results"]["company"]["officers"]
    except (KeyError, TypeError):
        return None

    # Prefer officers whose position implies ownership.
    ranked = sorted(officers, key=lambda o: _officer_rank(o))
    for wrap in ranked:
        off = wrap.get("officer") or {}
        raw = off.get("name") or ""
        cand = _clean_name(raw)
        if cand:
            return cand
    return None


def _officer_rank(wrap) -> int:
    off = (wrap or {}).get("officer") or {}
    pos = (off.get("position") or "").lower()
    for i, kw in enumerate(OWNER_HINTS):
        if kw in pos:
            return i
    return len(OWNER_HINTS) + 1


# --- Entry point -----------------------------------------------------------

def resolve_owner(
    html: str,
    business_name: str,
    city: str = "",
    state: str = "",
    website: str = "",
    phone: str = "",
    allow_bbb: bool = True,
    public_records_lookup=None,
) -> tuple[str, str]:
    """Return (owner_name, source).

    v2 cascade order (docs/prospect-domain.md §4):
      7a anchor  — business website (homepage already fetched, then subpages)
      7b public_records — free authoritative state license/SoS match (callable)
      7c bbb     — playwright/stealth BBB lookup
      (fallback) opencorporates — legacy free tier
    source is one of: "website", "public_records", "bbb", "opencorporates", "none".

    public_records_lookup, when provided, is `fn(state, business_name, city)`
    returning a dict with "owner_name" (or None). Passed in so non-prospect
    runs don't require Supabase env.
    """
    # 7a. Website anchor — zero extra network, cheapest, run first.
    if html:
        name = extract_owner_from_html(html)
        if name:
            return name, "website"
    if website:
        name = scrape_owner_subpages(website)
        if name:
            return name, "website"

    # 7b. Public records — free, authoritative, before the slow CF-prone BBB hop.
    if public_records_lookup and business_name and state:
        try:
            rec = public_records_lookup(state, business_name, city)
        except Exception:
            rec = None
        if rec and rec.get("owner_name"):
            return rec["owner_name"], "public_records"

    # 7c. BBB.
    if allow_bbb:
        name = find_owner_on_bbb(
            business_name, city=city, state=state,
            website=website, phone=phone,
        )
        if name:
            return name, "bbb"

    # Fallback. OpenCorporates (legacy free tier).
    if business_name:
        name = find_owner_on_opencorporates(business_name, state=state)
        if name:
            return name, "opencorporates"

    return "", "none"
