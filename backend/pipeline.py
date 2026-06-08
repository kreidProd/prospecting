"""
Reboot lead pipeline — implements the Tier Rubric v2
(docs/prospect-domain.md in reboot-automation-hub).

Signal layer (computed per lead BEFORE tiering — tiering is a pure
function of these):
- ads_live_verified / ad_evidence / ads_status (verified_live|history|none|unknown)
  — Transparency/Meta lookup errors or rate-limits yield 'unknown', never 'none'
- analytics_present — G-/UA-/GTM-. Capability only, NOT conversion tracking.
- conversion_tracking_present — Google Ads conversion event, Meta conversion
  event (fbq('track','Lead'|...) — base pixel alone does NOT count), or call
  tracking (CallRail / CTM / WhatConverts / Marchex / dynamic number swap)
- tracking_status (present|absent|unknown) — bare GTM container with no other
  tracking signals → 'unknown' (contents unverifiable)

Decision order (first match wins): SKIP dead site → RECHECK_ADS →
REVIEW_TRACKING → PARK → 1A/1B (single-loc wedge) → 3A/3B (multi-loc) →
2 → SKIP. gclid is demoted to bonus evidence (contributes to ad_evidence,
no longer required for 1B).

Outputs per row:
- tier, tier_reason, skip_reason, fit_score, score_breakdown
- ads_status, tracking_status, ad_evidence, analytics_present,
  conversion_tracking_present
- signal_* flags for each detector
- phone_verified (phone appeared on fetched page)
- location_count, ad_status_source, fetch_status
"""
import csv
import math
import re
import threading
import concurrent.futures
from typing import Callable, Optional

import requests

from ads_verifier import NULL_VERIFIER
from owner_extractor import resolve_owner
from reviews_lookup import lookup_reviews


# --- Qualifying floor constants (§5 locked levers, 2026-06-05) -------------
MIN_REVIEWS = 40              # Tier 2 qualifying floor (single-loc, no ads)
MIN_RATING = 4.0              # Tier 2 rating floor
TIER1_MIN_REVIEWS = 10        # fly-by-night guard on 1A/1B
MULTI_LOC_MIN_REVIEWS = 100
MULTI_LOC_MIN_RATING = 3.5    # no reputation-disaster 3As


# --- Detection patterns ---------------------------------------------------
# Three independent signal families (rubric v2):
#   analytics (capability)  vs  ad evidence (did/do they advertise)
#   vs conversion tracking (can they measure). A base pixel install is
# ad evidence but NOT conversion tracking — only a real conversion event
# (Google or Meta) or call tracking counts as "they can measure".
PATTERNS = {
    "google_ads_pixel": re.compile(
        r"(AW-\d{6,}|googleadservices\.com|gtag/js\?id=AW-)", re.I
    ),
    "conversion_event": re.compile(
        r"(gtag\s*\(\s*['\"]event['\"]\s*,\s*['\"]conversion['\"]"
        r"|send_to\s*:\s*['\"]AW-\d+/[\w-]+"
        r"|/collect\?.*_et=conversion)",
        re.I,
    ),
    # Meta conversion event: fbq('track', '<lead-gen event>'). A bare
    # fbq('init') / base pixel NEVER counts as conversion tracking.
    "meta_conversion_event": re.compile(
        r"fbq\s*\(\s*['\"]track['\"]\s*,\s*['\"]"
        r"(?:Lead|Contact|Schedule|SubmitApplication)['\"]",
        re.I,
    ),
    # Call tracking — roofers convert by phone; this is the signal most
    # likely to be missed. CallRail swap.js, CallTrackingMetrics,
    # WhatConverts, Marchex, or any generic dynamic-number-swap script.
    "call_tracking": re.compile(
        r"(cdn\.callrail\.com|callrail\.com/companies|companies/\d+/[\da-f]+/\d+/swap\.js"
        r"|calltrackingmetrics\.com|tctm\.co|ctm\.js"
        r"|whatconverts\.com|scripts\.iconnode\.com"
        r"|marchex\.(?:com|io)|voicestar\.com|mxapis\.com"
        r"|dynamic[\s_-]?number[\s_-]?(?:insertion|swap|replacement)|\bdni\.js)",
        re.I,
    ),
    "gtm": re.compile(r"(googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{5,})", re.I),
    "ga4": re.compile(r"(google-analytics\.com|gtag/js\?id=G-|G-[A-Z0-9]{6,})", re.I),
    "ua": re.compile(r"UA-\d{4,10}-\d{1,4}", re.I),
    "meta_pixel": re.compile(r"(connect\.facebook\.net|fbq\s*\()", re.I),
    # gclid: bonus evidence only (demoted in v2) — contributes to
    # ad_evidence but is no longer required/primary for 1B.
    "gclid_in_html": re.compile(r"[?&](?:gclid|gclsrc)=|utm_source=google", re.I),
    "locations_link": re.compile(
        r'href=["\'][^"\']*(?:/locations|/our-locations|/offices|/service-areas|/branches|/find-us)',
        re.I,
    ),
    "multi_location_phrase": re.compile(
        r"(our locations|find a location|multiple locations|\d+\s+locations|nationwide service)",
        re.I,
    ),
}

COLUMN_ALIASES = {
    "business_name": ["business_name", "name", "company", "company_name", "business", "title"],
    "phone": ["phone", "phone_number", "telephone", "phone_1", "contact_phone"],
    "email": ["email", "email_1", "email_address", "primary_email"],
    "website": ["website", "site", "url", "domain", "homepage"],
    "address": ["address", "street", "full_address", "street_address"],
    "city": ["city", "locality"],
    "state": ["state", "region"],
    "reviews": ["total_reviews", "reviews", "review_count", "ratings_count", "reviews_count"],
    "rating": ["rating", "avg_rating", "average_rating", "stars"],
    "owner_name": [
        "owner_name", "owner", "business_owner", "contact", "contact_name",
        "primary_contact", "point_of_contact", "poc", "decision_maker", "full_name",
    ],
}


# --- Helpers --------------------------------------------------------------

def _normalize_phone(p: str) -> str:
    if not p:
        return ""
    digits = re.sub(r"\D", "", p)
    return digits[-10:] if len(digits) >= 10 else ""


_COMMON_SUBDOMAINS = ("www", "m", "mobile", "en", "us", "web", "shop", "store", "blog")


def _normalize_domain(url: str) -> str:
    """Bare 'example.com'-style domain. Strips scheme, auth, path, query,
    fragment, port, trailing dot, and a single leading common subdomain."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^[a-z][a-z0-9+\-.]*://", "", u)
    u = u.split("@", 1)[-1]
    u = u.split("/", 1)[0]
    u = u.split("?", 1)[0]
    u = u.split("#", 1)[0]
    u = u.split(":", 1)[0]
    u = u.rstrip(".").strip()
    if not u:
        return ""
    parts = u.split(".")
    if len(parts) > 2 and parts[0] in _COMMON_SUBDOMAINS:
        parts = parts[1:]
    return ".".join(parts)


def _map_columns(headers):
    hmap = {(h or "").lower().strip(): h for h in headers}
    out = {}
    for canon, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in hmap:
                out[canon] = hmap[a]
                break
    return out


def _parse_int(val: str) -> int:
    if not val:
        return 0
    digits = re.sub(r"[^\d]", "", val)
    return int(digits) if digits else 0


def _parse_float(val: str) -> float:
    if not val:
        return 0.0
    m = re.search(r"(\d+\.?\d*)", val)
    return float(m.group(1)) if m else 0.0


def load_csv(path: str):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        mapping = _map_columns(reader.fieldnames or [])
        rows = list(reader)
    return [
        {canon: (r.get(src) or "").strip() for canon, src in mapping.items()}
        for r in rows
    ]


# --- Fetching + signal extraction ----------------------------------------

def fetch_page(url: str, timeout: float = 10.0):
    if not url:
        return None, "no_url"
    target = url if url.startswith("http") else "https://" + url.lstrip("/")
    try:
        r = requests.get(
            target,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RebootProspector/1.0)"},
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None, f"http_{r.status_code}"
        return r.text[:800_000], "ok"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.SSLError:
        return None, "ssl_error"
    except requests.exceptions.ConnectionError:
        return None, "conn_error"
    except requests.exceptions.RequestException:
        return None, "fetch_error"
    except Exception:
        return None, "unknown_error"


def detect_signals(html: str) -> dict:
    return {k: bool(p.search(html)) for k, p in PATTERNS.items()}


def detect_location_count(html: str) -> int:
    if not html:
        return 1
    loc_links = len(PATTERNS["locations_link"].findall(html))
    phrase = bool(PATTERNS["multi_location_phrase"].search(html))
    addr_re = re.compile(
        r"\b\d{3,5}\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s+"
        r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Dr|Drive|Way|Ln|Lane|Hwy)\b"
    )
    addresses = len(set(addr_re.findall(html)))
    if loc_links >= 2 or phrase or addresses >= 2:
        return max(2, loc_links, addresses)
    return 1


def _make_public_records_lookup():
    """Build the cascade-7b public-records lookup, or None if unavailable.

    Returns `fn(state, business_name, city) -> dict|None`. None unless Supabase
    env is set, so non-prospect runs (e.g. ClickUp re-tier) need no DB. Per lead:
    exact state+norm match (indexed) → city-narrowed fuzzy fallback.
    """
    import os
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")):
        return None
    try:
        from supabase_client import fetch_exact, fetch_state_city
        from records_loaders import match_public_record
    except Exception:
        return None

    def lookup(state, business_name, city=""):
        fstate = (lambda s, _: fetch_state_city(s, city)) if city else None
        return match_public_record(
            state, business_name, fetch_exact, fstate, city=city,
        )

    return lookup


def verify_phone_on_site(phone: str, html: str) -> bool:
    """Check whether the scraped phone number appears on the fetched page."""
    if not phone or not html:
        return False
    norm = _normalize_phone(phone)
    if not norm or len(norm) < 10:
        return False
    variants = [
        norm,
        f"{norm[:3]}.{norm[3:6]}.{norm[6:]}",
        f"{norm[:3]}-{norm[3:6]}-{norm[6:]}",
        f"({norm[:3]}) {norm[3:6]}-{norm[6:]}",
        f"({norm[:3]}){norm[3:6]}-{norm[6:]}",
        f"{norm[:3]} {norm[3:6]} {norm[6:]}",
        f"1{norm}",
        f"+1{norm}",
    ]
    lower = html.lower()
    for v in variants:
        if v.lower() in lower:
            return True
    # Final pass: strip all non-digits from HTML and search
    return norm in re.sub(r"\D", "", html)


# --- v2 signal layer (compute per lead BEFORE tiering) ---------------------

# Verifier errors that mean "no lookup was attempted" rather than "the
# lookup failed". These must NOT poison ads_status to 'unknown' — a run
# with live-ad verification disabled would otherwise route every lead to
# RECHECK_ADS. Semantic non-errors from the Playwright verifier
# ("never_ran", "stale", "no_detail_open", "no_last_shown") are also not
# lookup failures — they carry a definitive ever_advertised answer.
_VERIFIER_NON_ERRORS = {
    None, "", "disabled", "not_configured", "no_query", "no_url", "no_domain",
    "never_ran", "stale", "no_detail_open", "no_last_shown",
}


def _verifier_failed(res: dict) -> bool:
    """True when a verifier attempted a lookup and it errored/rate-limited."""
    return (res.get("error") or None) not in _VERIFIER_NON_ERRORS


def compute_v2_signals(signals: Optional[dict], g_res: dict, m_res: dict) -> dict:
    """Derive the rubric-v2 signal set from tag detection + ad verifiers.

    Returns a dict with:
      ads_live_verified (bool)         — Transparency/Meta confirmed a LIVE ad
      ad_evidence (bool)               — any of: live, Transparency history,
                                         AW- tag, Meta base pixel, gclid (bonus)
      ads_status (str)                 — verified_live | history | none | unknown
                                         (lookup errors → 'unknown', never 'none')
      analytics_present (bool)         — G-/UA-/GTM-. Capability only.
      conversion_tracking_present (bool) — Google Ads conversion event, Meta
                                         conversion event, or call tracking
      tracking_status (str)            — present | absent | unknown
                                         (bare GTM container → 'unknown')
    """
    s = signals or {}

    ads_live_verified = bool(g_res.get("live") or m_res.get("live"))
    ever_advertised = bool(g_res.get("ever_advertised"))

    # Tag-side ad evidence: AW- tag, Meta base pixel, gclid (bonus only —
    # demoted in v2: contributes here but never drives a tier by itself).
    tag_ad_evidence = bool(
        s.get("google_ads_pixel") or s.get("meta_pixel") or s.get("gclid_in_html")
    )
    ad_evidence = ads_live_verified or ever_advertised or tag_ad_evidence

    # ads_status — state of the live/history LOOKUP. A failed or rate-limited
    # lookup is 'unknown' (requeue) unless another source already produced
    # positive evidence; failed lookup ≠ "no ads".
    if ads_live_verified:
        ads_status = "verified_live"
    elif ever_advertised:
        ads_status = "history"
    elif _verifier_failed(g_res) or _verifier_failed(m_res):
        ads_status = "unknown"
    else:
        ads_status = "none"

    analytics_present = bool(s.get("ga4") or s.get("ua") or s.get("gtm"))

    conversion_tracking_present = bool(
        s.get("conversion_event")
        or s.get("meta_conversion_event")
        or s.get("call_tracking")
    )

    # tracking_status — 'unknown' only when GTM is the SOLE tag on the page:
    # the container is present but its contents can't be verified from HTML
    # alone, so we can't say absent. If any other tag (AW-, G-/UA-, Meta
    # pixel) is visible alongside, the page exposes its stack and we trust
    # the absence of conversion events.
    if conversion_tracking_present:
        tracking_status = "present"
    elif s.get("gtm") and not (
        s.get("google_ads_pixel") or s.get("ga4") or s.get("ua") or s.get("meta_pixel")
    ):
        tracking_status = "unknown"
    else:
        tracking_status = "absent"

    return {
        "ads_live_verified": ads_live_verified,
        "ad_evidence": ad_evidence,
        "ads_status": ads_status,
        "analytics_present": analytics_present,
        "conversion_tracking_present": conversion_tracking_present,
        "tracking_status": tracking_status,
    }


# --- Tier classification (rubric v2 — deterministic, first match wins) -----

def classify_tier(
    row: dict,
    signals: Optional[dict],
    locations: int,
    status: str,
    v2: Optional[dict] = None,
):
    """Return (tier, tier_reason) per the v2 decision order.

    `v2` is the signal dict from compute_v2_signals(). Tiering is a pure
    function of the precomputed signals — no raw verifier results in here.

    Decision order (first match wins):
      dead/no site                                  → SKIP
      ads lookup errored                            → RECHECK_ADS (requeue)
      bare GTM, contents unverifiable               → REVIEW_TRACKING (human)
      ad evidence + already tracking conversions    → PARK (different pitch)
      single-loc wedge (advertising + blind)        → 1A / 1B
      multi-loc with footprint                      → 3A / 3B / SKIP
      single-loc qualified, no ads                  → 2
      else                                          → SKIP
    """
    reviews = _parse_int(row.get("reviews", ""))
    rating = _parse_float(row.get("rating", ""))
    has_website = bool(row.get("website"))

    if not has_website or status != "ok" or not signals:
        return "SKIP", "no_or_dead_site"

    v = v2 or compute_v2_signals(signals, {}, {})

    # UNKNOWN routing — never finalize a 1A on bad data.
    if v["ads_status"] == "unknown":
        return "RECHECK_ADS", "ads_lookup_failed"          # requeue, don't down-tier
    if v["tracking_status"] == "unknown":
        return "REVIEW_TRACKING", "gtm_contents_unverified"  # human eyes

    # PARK — proven spender already tracking. Good buyer, wrong wedge.
    if v["ad_evidence"] and v["conversion_tracking_present"]:
        return "PARK", "already_tracking_conversions"

    # TIER 1 — single-loc wedge: advertising + blind (≥10 reviews guard).
    if (
        locations < 2
        and reviews >= TIER1_MIN_REVIEWS
        and not v["conversion_tracking_present"]
    ):
        if v["ads_status"] == "verified_live":
            return "1A", "live_ad_no_conversion_tracking"
        if v["ad_evidence"]:
            return "1B", "past_ad_evidence_no_conversion_tracking"

    # TIER 3 — multi-loc.
    if (
        locations >= 2
        and reviews >= MULTI_LOC_MIN_REVIEWS
        and rating >= MULTI_LOC_MIN_RATING
    ):
        if v["ad_evidence"] or v["ads_status"] == "verified_live":
            return "3A", "multiloc_ad_evidence"
        if v["analytics_present"]:
            return "3B", "multiloc_analytics_only"
        return "SKIP", "multiloc_no_digital"

    # TIER 2 — single-loc qualified, no ads.
    if (
        locations < 2
        and reviews >= MIN_REVIEWS
        and rating >= MIN_RATING
        and not v["ad_evidence"]
    ):
        return "2", "qualified_single_loc_no_ads"

    return "SKIP", "below_thresholds"


# --- Fit scoring ----------------------------------------------------------

SCORE_WEIGHTS = {
    "google_ads_active": 35,
    "verified_live_ad": 15,  # bonus on top of pixel signal when confirmed live
    "gclid_traffic": 10,
    "no_conversion_tracking": 20,
    "meta_pixel": 8,
    "mature_analytics": 8,
    "review_volume": 15,
    "high_rating": 10,
    "phone_verified": 6,
    "email_present": 3,
}


def compute_fit_score(row: dict, signals: dict, tier: str, phone_verified: bool, v2: Optional[dict] = None):
    """Return (score_0_to_100, breakdown_tokens).

    Weights unchanged from v1; inputs are mapped onto the v2 signals:
      google_ads_active       ↔ ad_evidence (any ad footprint)
      verified_live_ad        ↔ ads_live_verified
      no_conversion_tracking  ↔ ad_evidence AND not conversion_tracking_present
    """
    if tier == "SKIP":
        return 0, ["skip"]

    v = v2 or {}
    score = 0
    breakdown = []

    def add(weight: int, label: str):
        nonlocal score
        score += weight
        breakdown.append(f"+{weight} {label}")

    has_ads_active = bool(v.get("ad_evidence"))
    if has_ads_active:
        add(SCORE_WEIGHTS["google_ads_active"], "google_ads_active")

    if v.get("ads_live_verified"):
        add(SCORE_WEIGHTS["verified_live_ad"], "verified_live_ad (transparency)")

    if signals.get("gclid_in_html"):
        add(SCORE_WEIGHTS["gclid_traffic"], "paid_traffic_evidence")

    if has_ads_active and not v.get("conversion_tracking_present"):
        add(SCORE_WEIGHTS["no_conversion_tracking"], "no_conversion_tracking (ICP)")

    if signals.get("meta_pixel"):
        add(SCORE_WEIGHTS["meta_pixel"], "meta_pixel")

    if signals.get("ga4") and signals.get("gtm"):
        add(SCORE_WEIGHTS["mature_analytics"], "mature_analytics")

    reviews = _parse_int(row.get("reviews", ""))
    if reviews >= MIN_REVIEWS:
        # Log-scaled: 40 reviews ~ 11 pts, 100 ~ 14 pts, 500 ~ 18 pts (capped)
        rv = min(SCORE_WEIGHTS["review_volume"], int(math.log10(max(reviews, 1)) * 7))
        add(rv, f"reviews:{reviews}")

    rating = _parse_float(row.get("rating", ""))
    if rating >= 4.5:
        add(SCORE_WEIGHTS["high_rating"], f"rating:{rating}")
    elif rating >= 4.0:
        add(SCORE_WEIGHTS["high_rating"] // 2, f"rating:{rating}")

    if phone_verified:
        add(SCORE_WEIGHTS["phone_verified"], "phone_on_site")

    if row.get("email"):
        add(SCORE_WEIGHTS["email_present"], "email_present")

    return min(100, score), breakdown


# --- Main pipeline --------------------------------------------------------

def run_pipeline(
    scraped_path: str,
    existing_path: Optional[str],
    output_path: str,
    workers: int = 20,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    timeout: float = 10.0,
    target_tiers: Optional[list] = None,
    existing_rows: Optional[list] = None,
    google_verifier=None,
    meta_verifier=None,
    owner_only: bool = False,
    foundation_path: Optional[str] = None,
    google_places_api_key: str = "",
):
    scraped = load_csv(scraped_path)
    existing = list(existing_rows) if existing_rows else []
    if existing_path:
        existing.extend(load_csv(existing_path))

    # Foundation rows: re-tiered like prospects AND used to dedup the
    # prospect CSV. They sit at the top of the output so the foundation
    # stays the foundation.
    foundation: list[dict] = []
    if foundation_path:
        foundation = load_csv(foundation_path)

    existing_phones = {_normalize_phone(r.get("phone", "")) for r in existing}
    existing_phones.discard("")
    existing_domains = {_normalize_domain(r.get("website", "")) for r in existing}
    existing_domains.discard("")

    # Foundation contributes its own phone/domain into the dedup pool so
    # prospect rows that overlap with foundation get dropped.
    foundation_phones = {_normalize_phone(r.get("phone", "")) for r in foundation}
    foundation_phones.discard("")
    foundation_domains = {_normalize_domain(r.get("website", "")) for r in foundation}
    foundation_domains.discard("")

    dedup_source_size = len(existing) + len(foundation)

    unique = []
    dup_count = 0
    foundation_collisions = 0
    for r in scraped:
        phone = _normalize_phone(r.get("phone", ""))
        domain = _normalize_domain(r.get("website", ""))
        if (phone and phone in existing_phones) or (domain and domain in existing_domains):
            dup_count += 1
            continue
        if (phone and phone in foundation_phones) or (domain and domain in foundation_domains):
            foundation_collisions += 1
            continue
        unique.append(r)

    # Re-tier foundation + deduped prospects together. Foundation first so
    # its rows lead the output CSV.
    unique = list(foundation) + unique

    total = len(unique)
    results = []
    lock = threading.Lock()
    counter = {"n": 0}

    google = google_verifier or NULL_VERIFIER
    meta = meta_verifier or NULL_VERIFIER

    # Public-records owner lookup (cascade 7b). Built once per run; None unless
    # Supabase env is present, so non-prospect runs need no DB. Exact match is
    # indexed/fast; fuzzy is city-narrowed to keep per-lead result sets small.
    pr_lookup = _make_public_records_lookup()

    # Transparency Center via Apify is a batched pre-pass: one actor run covers
    # every domain in this pipeline, cheaper and faster than per-row lookups.
    # Google verification is now per-row inside process() via a sync actor call.
    # Each worker looks up its own domain; results are cached on the verifier
    # so repeats across a run are free.

    # Emit an initial progress event so the UI shows `total` immediately
    # instead of sitting at "0/?" until the first fetch completes.
    if on_progress:
        on_progress(0, total, "starting")

    def process_owner_only(row):
        clean_site = _normalize_domain(row.get("website", ""))
        if clean_site:
            row["website"] = clean_site
        out = dict(row)
        existing_owner = (row.get("owner_name") or "").strip()
        if existing_owner:
            owner_name, owner_source = existing_owner, "csv"
        else:
            # Fetch the homepage so the website-based fallbacks in
            # resolve_owner have something to parse when BBB misses.
            homepage_html = ""
            if row.get("website"):
                homepage_html, _ = fetch_page(row["website"], timeout=timeout)
            owner_name, owner_source = resolve_owner(
                homepage_html or "",
                business_name=row.get("business_name", ""),
                city=row.get("city", ""),
                state=row.get("state", ""),
                website=row.get("website", ""),
                phone=row.get("phone", ""),
                allow_bbb=True,
                public_records_lookup=pr_lookup,
            )
        out["owner_name"] = owner_name
        out["owner_source"] = owner_source
        # Preserve any tier already in the CSV so downstream filters/UI work
        out.setdefault("tier", row.get("tier", ""))
        out.setdefault("preliminary_tier_hint", out.get("tier", ""))
        with lock:
            counter["n"] += 1
            if on_progress:
                on_progress(counter["n"], total, out.get("tier") or "owner_lookup")
        return out

    def process(row):
        clean_site = _normalize_domain(row.get("website", ""))
        if clean_site:
            row["website"] = clean_site

        # Reviews / rating enrichment — only when missing. Looks up via
        # Google Places API (if configured) and falls back to BBB. Run
        # before tier classification so the freshly-found numbers actually
        # influence the tier assignment.
        reviews_now = _parse_int(row.get("reviews", ""))
        rating_now = _parse_float(row.get("rating", ""))
        reviews_source = ""
        if reviews_now == 0 or rating_now == 0:
            r_count, r_rating, r_src = lookup_reviews(
                business_name=row.get("business_name", ""),
                city=row.get("city", ""),
                state=row.get("state", ""),
                bbb_profile_html="",
                google_places_api_key=google_places_api_key,
            )
            if r_src != "none":
                if reviews_now == 0 and r_count:
                    row["reviews"] = str(r_count)
                if rating_now == 0 and r_rating:
                    row["rating"] = f"{r_rating:.1f}"
                reviews_source = r_src

        html, status = fetch_page(row.get("website", ""), timeout=timeout)
        signals = detect_signals(html) if html else None
        locations = detect_location_count(html) if html else 1
        phone_verified = verify_phone_on_site(row.get("phone", ""), html or "")

        domain = row.get("website", "")
        biz = row.get("business_name", "")
        g_res = google.verify(domain, biz) if domain else {"live": False, "ad_count": 0, "source": "google", "error": "no_url"}
        m_res = meta.verify(domain, biz) if (domain or biz) else {"live": False, "ad_count": 0, "source": "meta", "error": "no_url"}

        # v2 signal layer — computed BEFORE tiering; tiering is a pure
        # function of these.
        v2 = compute_v2_signals(signals, g_res, m_res)

        tier, tier_reason = classify_tier(row, signals, locations, status, v2=v2)
        score, breakdown = compute_fit_score(
            row, signals or {}, tier, phone_verified, v2=v2
        )

        out = dict(row)
        out["fetch_status"] = status
        out["reviews_source"] = reviews_source
        out["tier"] = tier
        out["tier_reason"] = tier_reason
        # Legacy column: populated only on SKIP so older UI/CSV consumers
        # keep their "why was this skipped" behavior.
        out["skip_reason"] = tier_reason if tier == "SKIP" else ""
        out["fit_score"] = str(score)
        out["score_breakdown"] = "; ".join(breakdown)
        out["phone_verified"] = "yes" if phone_verified else "no"
        out["location_count"] = str(locations)

        # v2 signal columns
        out["ads_status"] = v2["ads_status"]
        out["tracking_status"] = v2["tracking_status"]
        out["ad_evidence"] = "yes" if v2["ad_evidence"] else "no"
        out["analytics_present"] = "yes" if v2["analytics_present"] else "no"
        out["conversion_tracking_present"] = (
            "yes" if v2["conversion_tracking_present"] else "no"
        )

        # Owner name — prefer an existing value from the incoming CSV
        # (ClickUp re-tier often already has an owner/contact column).
        # Only scrape when the row didn't supply one.
        existing_owner = (row.get("owner_name") or "").strip()
        if existing_owner:
            owner_name, owner_source = existing_owner, "csv"
        else:
            owner_name, owner_source = resolve_owner(
                html or "",
                business_name=row.get("business_name", ""),
                city=row.get("city", ""),
                state=row.get("state", ""),
                website=row.get("website", ""),
                phone=row.get("phone", ""),
                allow_bbb=True,
                public_records_lookup=pr_lookup,
            )
        out["owner_name"] = owner_name
        out["owner_source"] = owner_source

        # Live-ad verification fields
        out["google_ads_live"] = "yes" if g_res.get("live") else "no"
        out["google_ads_count"] = str(g_res.get("ad_count", 0))
        out["google_ads_error"] = g_res.get("error") or ""
        # Extra signal: did they ever run ads on Google, even historically?
        if "ever_advertised" in g_res:
            out["google_ads_ever"] = "yes" if g_res.get("ever_advertised") else "no"
            out["google_ads_count_all_time"] = str(g_res.get("ad_count_all_time", 0))
            out["google_ads_count_30d"] = str(g_res.get("ad_count_30d", 0))
            if not g_res.get("ever_advertised"):
                out["google_ads_status"] = "never_ran"
            elif g_res.get("live"):
                out["google_ads_status"] = "active"
            else:
                out["google_ads_status"] = "stale"
        out["meta_ads_live"] = "yes" if m_res.get("live") else "no"
        out["meta_ads_count"] = str(m_res.get("ad_count", 0))
        out["meta_ads_error"] = m_res.get("error") or ""

        # Tell the UI which sources actually contributed
        sources = []
        if signals:
            sources.append("tag_detection")
        if google.configured and not g_res.get("error"):
            sources.append("google_transparency")
        if meta.configured and not m_res.get("error"):
            sources.append("meta_ad_library")
        out["ad_status_source"] = ",".join(sources) or "n/a"

        keys = (
            "google_ads_pixel", "conversion_event", "meta_conversion_event",
            "call_tracking", "gtm", "ga4", "ua", "meta_pixel", "gclid_in_html",
        )
        for k in keys:
            out[f"signal_{k}"] = ("yes" if signals.get(k) else "no") if signals else ""

        # Legacy field for backward compat with older UI code paths
        out["preliminary_tier_hint"] = tier

        with lock:
            counter["n"] += 1
            if on_progress:
                on_progress(counter["n"], total, tier)
        return out

    worker_fn = process_owner_only if owner_only else process
    if total:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(worker_fn, unique):
                results.append(res)

    # Sort by score desc so the table shows hottest prospects first
    results.sort(key=lambda r: int(r.get("fit_score") or "0"), reverse=True)

    dist = {}
    for r in results:
        t = r["tier"]
        dist[t] = dist.get(t, 0) + 1

    # Apply target tier filter to the downloaded CSV (keep `results` full for the UI,
    # so users can still unfilter the table to audit what was skipped).
    csv_rows = results
    if target_tiers:
        target_set = set(target_tiers)
        csv_rows = [r for r in results if r["tier"] in target_set]

    if csv_rows:
        fields = list(csv_rows[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(csv_rows)

    return {
        "total_rows": len(scraped),
        "duplicates": dup_count,
        "foundation_size": len(foundation),
        "foundation_collisions": foundation_collisions,
        "dedup_source_size": dedup_source_size,
        "processed": len(results),
        "exported": len(csv_rows),
        "target_tiers": target_tiers,
        "tier_distribution": dist,
        "output_path": output_path,
        "results": results,
    }


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--scraped", required=True)
    ap.add_argument("--existing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=20)
    args = ap.parse_args()

    def progress(p, t, tier):
        if p % 25 == 0 or p == t:
            print(f"  {p}/{t} · last tier: {tier}")

    summary = run_pipeline(args.scraped, args.existing, args.out, args.workers, progress)
    summary.pop("results", None)
    print(json.dumps(summary, indent=2))
