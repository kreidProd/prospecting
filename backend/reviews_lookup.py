"""
Review-count and rating lookup for businesses missing those fields.

Cascade (highest precision first):
  1. Google Places API   — official, reliable. Requires google_places_api_key
                           setting. ~$34 per 1k lookups; first $200/mo free.
  2. Google Maps scrape  — Playwright-driven; reuses the transparency
                           verifier's worker pool style. Free but slower
                           and rate-limited per IP.
  3. BBB profile         — pulls the customer-review count and the
                           A+/A/B/etc. letter rating that BBB shows on the
                           profile page. Free (we already fetch this page
                           for owner extraction so it's effectively zero
                           network when chained).

Returns a tuple ``(reviews, rating, source)`` where reviews is an int and
rating is a float (1-5 stars). Source is one of "google_places",
"google_maps", "bbb", "none".

The pipeline calls this only when a row arrives with reviews=0 or rating=0
so businesses already enriched aren't double-billed.
"""
from __future__ import annotations

import re
from html import unescape
from typing import Optional, Tuple
from urllib.parse import quote

import requests


PLACES_FIND_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"

DEFAULT_TIMEOUT = 8.0

ReviewsResult = Tuple[int, float, str]


# --- Strategy 1: Google Places API ---------------------------------------

def find_via_google_places(
    business_name: str,
    city: str = "",
    state: str = "",
    api_key: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[ReviewsResult]:
    if not api_key or not business_name:
        return None

    parts = [business_name]
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    query = ", ".join(parts)

    try:
        r = requests.get(
            PLACES_FIND_URL,
            params={
                "input": query,
                "inputtype": "textquery",
                "fields": "place_id,name,rating,user_ratings_total",
                "key": api_key,
            },
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None

    candidates = data.get("candidates") or []
    if not candidates:
        return None

    top = candidates[0]
    rating = float(top.get("rating") or 0)
    reviews = int(top.get("user_ratings_total") or 0)
    if reviews or rating:
        return reviews, rating, "google_places"
    return None


# --- Strategy 3: BBB profile (free) --------------------------------------

# BBB letter grade -> approximate 1-5 star translation. Not a true review
# rating, but better than 0 when nothing else is available. Documented
# explicitly on the row via reviews_source so users know.
BBB_GRADE_TO_STARS = {
    "A+": 5.0, "A": 4.7, "A-": 4.5,
    "B+": 4.2, "B": 4.0, "B-": 3.8,
    "C+": 3.5, "C": 3.3, "C-": 3.0,
    "D+": 2.5, "D": 2.0, "D-": 1.7,
    "F": 1.0, "NR": 0.0,
}


def find_via_bbb(profile_html: str) -> Optional[ReviewsResult]:
    """Parse review count + BBB letter grade from a fetched BBB profile.

    Pass the same HTML that owner_extractor fetched — no extra network."""
    if not profile_html:
        return None

    grade = _extract_bbb_grade(profile_html)
    reviews = _extract_bbb_review_count(profile_html)
    if grade is None and not reviews:
        return None
    rating = BBB_GRADE_TO_STARS.get(grade or "", 0.0)
    return reviews or 0, rating, "bbb"


def _extract_bbb_grade(html: str) -> Optional[str]:
    # BBB renders the letter grade inside an element near the text "BBB Rating".
    # The simplest reliable signal is the grade-letter string itself appearing
    # close to that label. Try a couple of patterns.
    patterns = [
        r"BBB\s+Rating[^<]{0,200}?>\s*([A-F][+\-]?|NR)\s*<",
        r'class="[^"]*rating[^"]*"[^>]*>\s*([A-F][+\-]?|NR)\s*<',
        r">\s*([A-F][+\-]?|NR)\s*<\s*/[a-z]+>\s*Reasons\s+for\s+rating",
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            grade = m.group(1).upper().replace(" ", "")
            if grade in BBB_GRADE_TO_STARS:
                return grade
    return None


def _extract_bbb_review_count(html: str) -> int:
    # Look for things like "Customer Reviews: 12" or "12 Customer Reviews"
    patterns = [
        r"Customer\s+Reviews?\s*[:\-]?\s*(\d+)",
        r"(\d+)\s+Customer\s+Reviews?",
        r'"reviewCount"\s*:\s*"?(\d+)"?',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return 0


# --- Public entry point ---------------------------------------------------

def lookup_reviews(
    business_name: str,
    city: str = "",
    state: str = "",
    bbb_profile_html: str = "",
    google_places_api_key: str = "",
) -> ReviewsResult:
    """Try sources in order, return the first non-empty hit.

    Returns ``(0, 0.0, "none")`` when nothing turns up.
    """
    if google_places_api_key:
        hit = find_via_google_places(
            business_name, city=city, state=state,
            api_key=google_places_api_key,
        )
        if hit:
            return hit

    if bbb_profile_html:
        hit = find_via_bbb(bbb_profile_html)
        if hit:
            return hit

    return 0, 0.0, "none"
