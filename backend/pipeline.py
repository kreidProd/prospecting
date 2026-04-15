"""
Reboot lead pipeline v2 — matches the six-tier classification spec.

Decision tree:
- Qualifying floor (single-loc): 40+ reviews, 4.0+ rating
- Multi-location floor: 100+ reviews across locations
- Ad activity (tag-based proxy until Transparency Center API is wired):
    pixel installed OR gclid evidence -> "currently/recently running"
- Conversion tracking distinction: AW- pixel alone != conversion event firing

Outputs per row:
- tier, skip_reason, fit_score, score_breakdown
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


# --- Qualifying floor constants -------------------------------------------
MIN_REVIEWS = 40
MIN_RATING = 4.0
MULTI_LOC_MIN_REVIEWS = 100


# --- Detection patterns ---------------------------------------------------
# Separating "pixel installed" (weak signal, often stale) from
# "conversion event defined" (strong signal, indicates mature tracking).
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
    "gtm": re.compile(r"(googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{5,})", re.I),
    "ga4": re.compile(r"(google-analytics\.com|gtag/js\?id=G-|G-[A-Z0-9]{6,})", re.I),
    "meta_pixel": re.compile(r"(connect\.facebook\.net|fbq\s*\()", re.I),
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
}


# --- Helpers --------------------------------------------------------------

def _normalize_phone(p: str) -> str:
    if not p:
        return ""
    digits = re.sub(r"\D", "", p)
    return digits[-10:] if len(digits) >= 10 else ""


def _normalize_domain(url: str) -> str:
    if not url:
        return ""
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].strip()


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


# --- Tier classification --------------------------------------------------

def classify_tier(
    row: dict,
    signals: Optional[dict],
    locations: int,
    status: str,
    verified_live: bool = False,
):
    """Return (tier, skip_reason). skip_reason is '' unless tier == 'SKIP'.

    `verified_live` is set True when Transparency Center or Meta Ads Library
    confirmed at least one live ad. That promotes 1B→1A (and 3B→3A on
    multi-loc) instead of the old gclid-in-HTML proxy.
    """
    reviews = _parse_int(row.get("reviews", ""))
    rating = _parse_float(row.get("rating", ""))
    has_website = bool(row.get("website"))

    if not has_website:
        return "SKIP", "no_website"
    if status != "ok" or not signals:
        return "SKIP", f"site_unreachable:{status}"

    # --- Multi-location branch ---
    if locations >= 2:
        if reviews < MULTI_LOC_MIN_REVIEWS:
            return "SKIP", f"multi_loc_low_reviews({reviews})"
        has_pixel = signals.get("google_ads_pixel") or signals.get("meta_pixel")
        has_ad_history = has_pixel or signals.get("gclid_in_html") or verified_live
        if has_ad_history:
            return "3A", ""
        if signals.get("ga4") or signals.get("gtm"):
            return "3B", ""
        return "SKIP", "multi_loc_no_digital"

    # --- Single-location qualifying floor ---
    if reviews < MIN_REVIEWS:
        return "SKIP", f"low_reviews({reviews})"
    if rating and rating < MIN_RATING:
        return "SKIP", f"low_rating({rating})"

    has_pixel = signals.get("google_ads_pixel", False)
    has_conversion = signals.get("conversion_event", False)
    has_gclid = signals.get("gclid_in_html", False)
    has_analytics = signals.get("ga4") or signals.get("gtm")

    currently_running = has_pixel or has_gclid or verified_live
    if currently_running:
        if has_conversion:
            return "SKIP", "already_tracking_conversions"
        # 1A requires confirmed live ad (Transparency Center or Meta Ad Library)
        # OR gclid evidence as legacy proxy. Otherwise pixel-only -> 1B.
        if verified_live or has_gclid:
            return "1A", ""
        return "1B", ""

    if has_analytics:
        return "2B", ""
    return "2A", ""


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


def compute_fit_score(row: dict, signals: dict, tier: str, phone_verified: bool, verified_live: bool = False):
    """Return (score_0_to_100, breakdown_tokens)."""
    if tier == "SKIP":
        return 0, ["skip"]

    score = 0
    breakdown = []

    def add(weight: int, label: str):
        nonlocal score
        score += weight
        breakdown.append(f"+{weight} {label}")

    has_ads_active = signals.get("google_ads_pixel") or signals.get("gclid_in_html") or verified_live
    if has_ads_active:
        add(SCORE_WEIGHTS["google_ads_active"], "google_ads_active")

    if verified_live:
        add(SCORE_WEIGHTS["verified_live_ad"], "verified_live_ad (transparency)")

    if signals.get("gclid_in_html"):
        add(SCORE_WEIGHTS["gclid_traffic"], "paid_traffic_evidence")

    if has_ads_active and not signals.get("conversion_event"):
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
):
    scraped = load_csv(scraped_path)
    existing = list(existing_rows) if existing_rows else []
    if existing_path:
        existing.extend(load_csv(existing_path))

    existing_phones = {_normalize_phone(r.get("phone", "")) for r in existing}
    existing_phones.discard("")
    existing_domains = {_normalize_domain(r.get("website", "")) for r in existing}
    existing_domains.discard("")
    dedup_source_size = len(existing)

    unique = []
    dup_count = 0
    for r in scraped:
        phone = _normalize_phone(r.get("phone", ""))
        domain = _normalize_domain(r.get("website", ""))
        if (phone and phone in existing_phones) or (domain and domain in existing_domains):
            dup_count += 1
            continue
        unique.append(r)

    total = len(unique)
    results = []
    lock = threading.Lock()
    counter = {"n": 0}

    google = google_verifier or NULL_VERIFIER
    meta = meta_verifier or NULL_VERIFIER

    # Transparency Center via Apify is a batched pre-pass: one actor run covers
    # every domain in this pipeline, cheaper and faster than per-row lookups.
    if google.configured and unique:
        if on_progress:
            on_progress(0, total, "verifying_google_ads")
        google.prefetch([r.get("website", "") for r in unique])

    # Emit an initial progress event so the UI shows `total` immediately
    # instead of sitting at "0/?" until the first fetch completes.
    if on_progress:
        on_progress(0, total, "starting")

    def process(row):
        html, status = fetch_page(row.get("website", ""), timeout=timeout)
        signals = detect_signals(html) if html else None
        locations = detect_location_count(html) if html else 1
        phone_verified = verify_phone_on_site(row.get("phone", ""), html or "")

        domain = row.get("website", "")
        biz = row.get("business_name", "")
        g_res = google.verify(domain, biz) if domain else {"live": False, "ad_count": 0, "source": "google", "error": "no_url"}
        m_res = meta.verify(domain, biz) if (domain or biz) else {"live": False, "ad_count": 0, "source": "meta", "error": "no_url"}
        verified_live = bool(g_res.get("live") or m_res.get("live"))

        tier, skip_reason = classify_tier(row, signals, locations, status, verified_live=verified_live)
        score, breakdown = compute_fit_score(
            row, signals or {}, tier, phone_verified, verified_live=verified_live
        )

        out = dict(row)
        out["fetch_status"] = status
        out["tier"] = tier
        out["skip_reason"] = skip_reason
        out["fit_score"] = str(score)
        out["score_breakdown"] = "; ".join(breakdown)
        out["phone_verified"] = "yes" if phone_verified else "no"
        out["location_count"] = str(locations)

        # Live-ad verification fields
        out["google_ads_live"] = "yes" if g_res.get("live") else "no"
        out["google_ads_count"] = str(g_res.get("ad_count", 0))
        out["google_ads_error"] = g_res.get("error") or ""
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

        keys = ("google_ads_pixel", "conversion_event", "gtm", "ga4", "meta_pixel", "gclid_in_html")
        for k in keys:
            out[f"signal_{k}"] = ("yes" if signals.get(k) else "no") if signals else ""

        # Legacy field for backward compat with older UI code paths
        out["preliminary_tier_hint"] = tier

        with lock:
            counter["n"] += 1
            if on_progress:
                on_progress(counter["n"], total, tier)
        return out

    if total:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(process, unique):
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
