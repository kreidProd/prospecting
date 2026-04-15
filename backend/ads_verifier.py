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
import time
from typing import Optional
from urllib.parse import quote

import requests


def _domain_only(url_or_domain: str) -> str:
    """Reduce any input to a bare 'example.com' form."""
    if not url_or_domain:
        return ""
    s = url_or_domain.lower().strip()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0]
    s = s.split("?")[0]
    return s.strip()


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
# Apify has multiple community actors that scrape adstransparency.google.com.
# We keep the actor ID in settings so the user can swap it if one breaks.
# Expected input shape (tested actors we've seen):
#     { "domains": ["example.com", ...], "region": "US" }
#     { "startUrls": [{"url": "https://adstransparency.google.com/?region=US&domain=..."}] }
# Output items are merged into a {domain -> result} map by `_merge_apify_items`.


class GoogleAdsApifyVerifier:
    def __init__(
        self,
        apify_client,
        actor_id: str,
        region: str = "US",
        run_timeout_seconds: int = 180,
    ):
        self.apify = apify_client
        self.actor_id = actor_id
        self.region = region
        self.run_timeout = run_timeout_seconds
        self._results: dict = {}
        self._error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.apify and self.actor_id)

    def prefetch(self, domains: list) -> None:
        """Run one Apify job for all domains and cache results on the instance."""
        if not self.configured:
            self._error = "not_configured"
            return
        clean = sorted({_domain_only(d) for d in domains if d})
        clean = [d for d in clean if d]
        if not clean:
            return

        try:
            run = self.apify.start_actor(
                self.actor_id,
                run_input={
                    "domains": clean,
                    "region": self.region,
                    "startUrls": [
                        {"url": f"https://adstransparency.google.com/?region={self.region}&domain={quote(d)}"}
                        for d in clean
                    ],
                },
            )
        except Exception as e:
            self._error = f"start_actor:{e}"
            return

        deadline = time.time() + self.run_timeout
        status = run.get("status")
        dataset_id = run.get("defaultDatasetId") or run.get("dataset_id")
        run_id = run.get("id")
        while status not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"):
            if time.time() > deadline:
                self._error = "run_timeout"
                return
            time.sleep(3)
            try:
                run = self.apify.get_run(run_id)
            except Exception as e:
                self._error = f"poll:{e}"
                return
            status = run.get("status")
            dataset_id = dataset_id or run.get("defaultDatasetId")

        if status != "SUCCEEDED" or not dataset_id:
            self._error = f"actor_{status.lower()}"
            return

        try:
            items = self.apify.get_dataset_items(dataset_id)
        except Exception as e:
            self._error = f"dataset:{e}"
            return

        self._results = _merge_apify_items(items)

    def verify(self, domain: str, business_name: str = "") -> dict:
        if not self.configured:
            return {"live": False, "ad_count": 0, "source": "google", "error": "not_configured"}
        if self._error:
            return {"live": False, "ad_count": 0, "source": "google", "error": self._error}
        res = self._results.get(_domain_only(domain))
        if res is None:
            return {"live": False, "ad_count": 0, "source": "google", "error": "no_data"}
        return {
            "live": res["ad_count"] > 0,
            "ad_count": res["ad_count"],
            "source": "google",
            "error": None,
        }


def _merge_apify_items(items: list) -> dict:
    """Normalize whatever the actor returns into {domain -> {ad_count}}.

    Actors vary. We try common field names: `domain`, `advertiser_domain`,
    `website`; and count ads via `ads`, `creatives`, `results`, or just by
    counting records per domain.
    """
    out: dict = {}
    for it in items or []:
        dom = _domain_only(
            it.get("domain") or it.get("advertiser_domain") or it.get("website") or it.get("url") or ""
        )
        if not dom:
            continue
        # Try to read an explicit ad count first
        count = None
        for key in ("ad_count", "adsCount", "total_ads", "ads_count"):
            if isinstance(it.get(key), int):
                count = it[key]
                break
        # Otherwise len() the usual list fields
        if count is None:
            for key in ("ads", "creatives", "results"):
                val = it.get(key)
                if isinstance(val, list):
                    count = len(val)
                    break
        # Last resort: each item = one ad
        if count is None:
            count = 1

        prev = out.get(dom, {"ad_count": 0})
        out[dom] = {"ad_count": prev["ad_count"] + count}
    return out


# --- Convenience: null verifier the pipeline can always call -------------

class _NullVerifier:
    configured = False
    def prefetch(self, domains): pass
    def verify(self, domain, business_name=""):
        return {"live": False, "ad_count": 0, "source": "none", "error": "disabled"}


NULL_VERIFIER = _NullVerifier()
