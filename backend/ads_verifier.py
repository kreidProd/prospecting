"""
Live-ad verification against public sources of truth.

Two independent verifiers, each safe to skip if not configured:

- MetaAdsVerifier   — direct calls to Meta's Ad Library Graph API.
                      Per-domain lookup runs inline in the enrichment pool.
                      Requires a user access token with ads_read permission.

- GoogleAdsApifyVerifier — batched pre-pass. Sends all domains to a single
                      Apify actor run (user-configurable actor ID).
                      Cheaper per-lead than per-domain Apify calls.

Both return the same shape:
    { "live": bool, "ad_count": int, "source": str, "error": str|None }

A stub verifier is returned when the source is unconfigured so the pipeline
code can always call `.verify(domain, business_name)` without branching.
"""
from __future__ import annotations

import re
import threading
from typing import Optional

import requests


_COMMON_SUBDOMAINS = ("www", "m", "mobile", "en", "us", "web", "shop", "store", "blog")


def _domain_only(url_or_domain: str) -> str:
    """Reduce any input to a bare 'example.com' form. Strips scheme, auth,
    path, query, fragment, port, trailing dot, and a single leading common
    subdomain (www, m, etc.)."""
    if not url_or_domain:
        return ""
    s = url_or_domain.strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+\-.]*://", "", s)
    s = s.split("@", 1)[-1]
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.split("#", 1)[0]
    s = s.split(":", 1)[0]
    s = s.rstrip(".").strip()
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) > 2 and parts[0] in _COMMON_SUBDOMAINS:
        parts = parts[1:]
    return ".".join(parts)


# --- Meta Ad Library ------------------------------------------------------
# Endpoint: https://graph.facebook.com/v19.0/ads_archive
# Docs:    https://www.facebook.com/ads/library/api/
# Auth:    User access token with `ads_read`, or system-user token for a verified app.


class MetaAdsVerifier:
    BASE = "https://graph.facebook.com/v19.0/ads_archive"

    def __init__(self, access_token: str, country: str = "US", timeout: float = 8.0):
        self.token = access_token
        self.country = country
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def verify(self, domain: str, business_name: str = "") -> dict:
        """Search the Ad Library for live ads referencing this domain or page name.

        Ad Library doesn't accept a domain filter directly, so we search by name
        and check that the resulting ads' page_id or linked destination aligns
        with the domain. For a first pass we trust a name match — false positives
        are rare for SMB roofers since Meta pages usually match their business.
        """
        if not self.configured:
            return {"live": False, "ad_count": 0, "source": "meta", "error": "not_configured"}

        query = (business_name or _domain_only(domain)).strip()
        if not query:
            return {"live": False, "ad_count": 0, "source": "meta", "error": "no_query"}

        params = {
            "access_token": self.token,
            "search_terms": query,
            "ad_reached_countries": f"['{self.country}']",
            "ad_active_status": "ACTIVE",
            "ad_type": "ALL",
            "fields": "id,page_id,page_name,ad_creative_link_captions",
            "limit": 25,
        }
        try:
            r = requests.get(self.BASE, params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            return {"live": False, "ad_count": 0, "source": "meta", "error": f"request:{type(e).__name__}"}

        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
            except Exception:
                msg = f"HTTP {r.status_code}"
            return {"live": False, "ad_count": 0, "source": "meta", "error": msg}

        data = r.json().get("data", []) or []
        dom = _domain_only(domain)
        # If we have a domain, require at least one creative caption to mention it.
        # This tightens false positives from common business names.
        if dom:
            matching = [
                ad for ad in data
                if _any_link_mentions(ad.get("ad_creative_link_captions") or [], dom)
            ]
            return {
                "live": len(matching) > 0,
                "ad_count": len(matching),
                "source": "meta",
                "error": None,
            }
        return {"live": len(data) > 0, "ad_count": len(data), "source": "meta", "error": None}


def _any_link_mentions(captions: list, dom: str) -> bool:
    for c in captions:
        if dom in (c or "").lower():
            return True
    return False


# --- Google Ads Transparency Center (via Apify) --------------------------
# Default actor: burbn~google-ads-search. Input shape:
#     { "countryCode": "US", "domain": "example.com", "format": "ALL", "maxResults": 40 }
# Output: one dataset item per ad (or a rollup with ad_count — see _count_ads).


class GoogleAdsApifyVerifier:
    """Per-domain verifier backed by burbn/google-ads-search (or similar).

    The chosen actor takes ONE domain per run and returns a dataset where each
    item is an ad. We call /run-sync-get-dataset-items once per prospect and
    cache the result by domain in-process so parallel workers never double-hit
    the same domain.
    """
    def __init__(
        self,
        apify_client,
        actor_id: str,
        region: str = "US",
        run_timeout_seconds: int = 120,
        max_results: int = 3,
    ):
        self.apify = apify_client
        self.actor_id = actor_id
        self.region = region
        self.run_timeout = run_timeout_seconds
        self.max_results = max_results
        self._cache: dict = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.apify and self.actor_id)

    def prefetch(self, domains: list) -> None:
        """No-op. Kept for API compatibility with the previous batched design."""
        return

    def verify(self, domain: str, business_name: str = "") -> dict:
        if not self.configured:
            return {"live": False, "ad_count": 0, "source": "google", "error": "not_configured"}
        dom = _domain_only(domain)
        if not dom:
            return {"live": False, "ad_count": 0, "source": "google", "error": "no_domain"}

        with self._lock:
            cached = self._cache.get(dom)
        if cached is not None:
            return cached

        try:
            items = self.apify.run_sync_get_dataset_items(
                self.actor_id,
                run_input={
                    "countryCode": self.region,
                    "domain": dom,
                    "format": "ALL",
                    "maxResults": self.max_results,
                },
                timeout=self.run_timeout,
            )
        except Exception as e:
            result = {"live": False, "ad_count": 0, "source": "google", "error": f"run:{e}"}
            with self._lock:
                self._cache[dom] = result
            return result

        ad_count = _count_ads(items)
        result = {
            "live": ad_count > 0,
            "ad_count": ad_count,
            "source": "google",
            "error": None,
        }
        with self._lock:
            self._cache[dom] = result
        return result


def _count_ads(items: list) -> int:
    """Count ads in a dataset. Most actors emit one item per ad so len() works,
    but some roll up totals — respect an explicit 'ad_count'-style field if present.
    """
    if not items:
        return 0
    first = items[0] if isinstance(items[0], dict) else {}
    for key in ("ad_count", "adsCount", "total_ads", "ads_count"):
        val = first.get(key)
        if isinstance(val, int) and val > 0:
            return val
    return len(items)


# --- Convenience: null verifier the pipeline can always call -------------

class _NullVerifier:
    configured = False
    def prefetch(self, domains): pass
    def verify(self, domain, business_name=""):
        return {"live": False, "ad_count": 0, "source": "none", "error": "disabled"}


NULL_VERIFIER = _NullVerifier()
