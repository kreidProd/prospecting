"""
Google Ads Transparency Center verifier — self-hosted with Playwright.

Loads adstransparency.google.com/advertiser?domain=X in a headless browser,
intercepts the internal SearchCreatives RPC response, and returns a creative
count. Cheaper than Apify for small/medium batches; needs residential proxies
at scale because Google will rate-limit a bare IP.

Interface matches GoogleAdsApifyVerifier / MetaAdsVerifier in ads_verifier.py:
    .configured : bool
    .prefetch(domains) : no-op here (per-domain lookups)
    .verify(domain, business_name) -> {live, ad_count, source, error}

Parallelism model: Playwright's sync API is greenlet-bound to the thread
that started it, so we can't share a browser across pipeline worker threads.
Instead we run a fixed-size pool of dedicated Playwright worker threads —
each owns its own sync_playwright / browser / context — all pulling jobs
from a single queue. Pipeline workers submit jobs and block for the result.

First use on a fresh machine:
    pip install playwright
    python -m playwright install chromium
"""
from __future__ import annotations

import json
import queue
import re
import threading
from typing import Optional
from urllib.parse import quote

try:
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
        Error as PlaywrightError,
    )
    _PLAYWRIGHT_IMPORT_ERROR = None
except ImportError as e:
    sync_playwright = None  # type: ignore
    PlaywrightTimeoutError = Exception  # type: ignore
    PlaywrightError = Exception  # type: ignore
    _PLAYWRIGHT_IMPORT_ERROR = e


# Transparency Center fires several internal RPCs while rendering a domain
# query: SearchAdvertisers (find the advertiser), SearchCreatives (list ads),
# GetCreative, etc. We watch all of them under /anji/_/rpc/ and union the
# creative IDs we find across every payload.
RPC_URL_SUBSTRING = "/anji/_/rpc/"
# Homepage-with-query is the URL Transparency Center navigates to when you
# type a domain in the search box. Accepts a `domain=` filter directly.
# All-time: no preset-date param → Transparency Center defaults to "All time"
PAGE_URL_ALL_TIME = (
    "https://adstransparency.google.com/?region={region}&domain={domain}"
)
# Recent: explicit Last 30 days preset
PAGE_URL_RECENT = (
    "https://adstransparency.google.com/?region={region}&domain={domain}"
    "&preset-date=Last+30+days"
)

_SHUTDOWN = object()


_COMMON_SUBDOMAINS = ("www", "m", "mobile", "en", "us", "web", "shop", "store", "blog")


def _domain_only(url_or_domain: str) -> str:
    """Reduce any input to a bare 'example.com'-style domain suitable for
    adstransparency.google.com. Strips scheme, auth, path, query, fragment,
    port, trailing dot, and a single leading common subdomain (www, m, etc.)."""
    if not url_or_domain:
        return ""
    s = url_or_domain.strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+\-.]*://", "", s)  # scheme://
    s = s.split("@", 1)[-1]  # user:pass@
    s = s.split("/", 1)[0]  # path
    s = s.split("?", 1)[0]  # query
    s = s.split("#", 1)[0]  # fragment
    s = s.split(":", 1)[0]  # port
    s = s.rstrip(".").strip()
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) > 2 and parts[0] in _COMMON_SUBDOMAINS:
        parts = parts[1:]
    return ".".join(parts)


class GoogleAdsTransparencyPlaywrightVerifier:
    """Count live creatives via the Transparency Center's internal RPC."""

    def __init__(
        self,
        region: str = "US",
        timeout_ms: int = 15000,
        headless: bool = True,
        user_agent: Optional[str] = None,
        concurrency: int = 4,
    ):
        self.region = region
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        self.concurrency = max(1, int(concurrency))
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        self._jobs: "queue.Queue[object]" = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._ready_count = 0
        self._ready_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._worker_errors: list[str] = []
        self._start_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return _PLAYWRIGHT_IMPORT_ERROR is None

    def prefetch(self, domains):
        return

    # --- Pool lifecycle --------------------------------------------------

    def _ensure_pool(self):
        if self._workers and all(w.is_alive() for w in self._workers):
            return
        with self._start_lock:
            if self._workers and all(w.is_alive() for w in self._workers):
                return
            if _PLAYWRIGHT_IMPORT_ERROR is not None:
                raise RuntimeError(
                    f"playwright not installed: {_PLAYWRIGHT_IMPORT_ERROR}. "
                    "Run: pip install playwright && python -m playwright install chromium"
                )
            self._workers = []
            self._ready_count = 0
            self._worker_errors = []
            self._ready_event.clear()
            for i in range(self.concurrency):
                t = threading.Thread(
                    target=self._run_worker,
                    name=f"pw-transparency-{i}",
                    daemon=True,
                )
                t.start()
                self._workers.append(t)
            # Wait for at least one worker to be ready (so jobs can be served).
            self._ready_event.wait(timeout=30)
            if self._ready_count == 0:
                err = "; ".join(self._worker_errors) or "unknown"
                raise RuntimeError(f"playwright pool failed to start: {err}")

    def _mark_ready(self):
        with self._ready_lock:
            self._ready_count += 1
            self._ready_event.set()

    def _run_worker(self):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                context = browser.new_context(user_agent=self.user_agent)
                self._mark_ready()
                while True:
                    item = self._jobs.get()
                    if item is _SHUTDOWN:
                        break
                    domain, reply_q = item  # type: ignore[misc]
                    try:
                        result = self._lookup_in_worker(context, domain)
                    except Exception as e:
                        result = {
                            "live": False,
                            "ad_count": 0,
                            "source": "google",
                            "error": f"worker: {e}",
                        }
                    try:
                        reply_q.put(result)
                    except Exception:
                        pass
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            self._worker_errors.append(str(e))
            # Unblock _ensure_pool even if this worker died before ready.
            self._ready_event.set()

    def _probe_url(self, context, url: str) -> dict:
        """Open one Transparency Center URL, return a dict of signals:
            reported_count: int — the page's own "N ads" header counter for
                                 the queried advertiser within the URL's date
                                 window. THIS IS THE TRUSTED SIGNAL.
            creatives: int   — unique creative IDs captured from RPCs
            advertisers: int — advertiser IDs referenced anywhere in RPCs
            rpc_ok: bool     — did any /anji/_/rpc/ response come through
            dom_cards: int   — visible ad/creative cards in the DOM
                              (UNRELIABLE — includes "Discover more on related
                              sites" suggestion tiles. Kept for diagnostics
                              only; do NOT use to decide live vs ended.)
            dom_empty: bool  — True if the rendered page shows the
                              "No ads found" empty-results banner
        """
        rpc_payloads: list[dict] = []
        advertiser_hits: list[str] = []

        def on_response(response):
            try:
                if RPC_URL_SUBSTRING not in response.url or response.status != 200:
                    return
                payload = _safe_parse_rpc(response.text())
                rpc_payloads.append(payload)
                if "SearchAdvertisers" in response.url:
                    for aid in _walk_advertiser_ids(payload):
                        if aid not in advertiser_hits:
                            advertiser_hits.append(aid)
            except Exception:
                pass

        page = context.new_page()
        page.on("response", on_response)

        dom_cards = 0
        dom_empty = False
        reported_count = None  # None means "couldn't read header"

        try:
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            try:
                dom_empty = page.locator("div.empty-results").count() > 0
            except Exception:
                dom_empty = False

            try:
                dom_cards = page.locator("creative-preview").count()
            except Exception:
                dom_cards = 0

            # The PAGE'S OWN "N ads" header is the source of truth. It
            # reflects only the queried advertiser's ads in the selected
            # window — does NOT include "related sites" suggestions, unlike
            # the creative-preview tile count.
            try:
                body_text = page.locator("body").inner_text(timeout=2000)
                m = re.search(r"\b(\d{1,5})\s+ads?\b", body_text, flags=re.IGNORECASE)
                if m:
                    try:
                        reported_count = int(m.group(1))
                    except ValueError:
                        pass
            except Exception:
                pass
        except (PlaywrightTimeoutError, PlaywrightError):
            try:
                page.close()
            except Exception:
                pass
            return {
                "reported_count": None, "creatives": 0, "advertisers": 0,
                "rpc_ok": False, "dom_cards": 0, "dom_empty": False,
            }
        finally:
            try:
                page.close()
            except Exception:
                pass

        return {
            "reported_count": reported_count,
            "creatives": _count_creatives(rpc_payloads),
            "advertisers": len(advertiser_hits),
            "rpc_ok": bool(rpc_payloads),
            "dom_cards": dom_cards,
            "dom_empty": dom_empty,
        }

    def _lookup_in_worker(self, context, domain: str) -> dict:
        """Click-into-creative tier discriminator.

        Open the TC list page for the domain. If no ads → Tier 2. Otherwise
        click the first creative-preview tile to load its detail page, parse
        the "Last shown: <Mon DD, YYYY>" line, and compare to today:
          last_shown ≥ today − 2 days  →  1A (currently active)
          older                         →  1B (ran but stopped)
          no creatives at all           →  2

        Two page navigations for 1A/1B leads, one for Tier 2.
        """
        import datetime

        all_url = PAGE_URL_ALL_TIME.format(domain=quote(domain), region=self.region)
        page = context.new_page()
        try:
            try:
                page.goto(all_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                return {"live": False, "ad_count": 0, "source": "google",
                        "error": f"nav: {e}", "ever_advertised": False,
                        "ad_count_all_time": 0, "ad_count_30d": 0}

            cps = page.locator("creative-preview")
            cp_count = cps.count()

            # Read header "N ads" — used for total count
            reported = None
            try:
                body = page.locator("body").inner_text(timeout=2000)
                m = re.search(r"\b(\d{1,5})\s+ads?\b", body, re.I)
                if m:
                    reported = int(m.group(1))
            except Exception:
                pass

            empty = page.locator("div.empty-results").count() > 0

            # Tier 2 short-circuit: no creatives AND empty results banner OR header says 0
            if cp_count == 0 or (empty and (reported is None or reported == 0)):
                return {"live": False, "ad_count": 0, "source": "google",
                        "error": "never_ran", "ever_advertised": False,
                        "ad_count_all_time": 0, "ad_count_30d": 0}

            # Click the first creative tile to get to the detail page
            try:
                cps.first.click(timeout=5000)
                page.wait_for_timeout(2500)
            except Exception:
                # Try anchor inside the tile as fallback
                try:
                    page.locator("creative-preview a").first.click(timeout=5000)
                    page.wait_for_timeout(2500)
                except Exception:
                    # Couldn't open detail — fall back to "advertiser exists" => 1B
                    return {"live": False, "ad_count": reported or cp_count,
                            "source": "google", "error": "no_detail_open",
                            "ever_advertised": True,
                            "ad_count_all_time": reported or cp_count,
                            "ad_count_30d": 0}

            # Parse "Last shown: <Mon DD, YYYY>"
            try:
                detail_body = page.locator("body").inner_text(timeout=3000)
            except Exception:
                detail_body = ""

            m = re.search(
                r"Last\s+shown\s*[:\-]?\s*([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})",
                detail_body,
            )
            last_shown = None
            if m:
                try:
                    last_shown = datetime.datetime.strptime(
                        f"{m.group(1)} {m.group(2)} {m.group(3)}",
                        "%b %d %Y",
                    ).date()
                except ValueError:
                    pass

            today = datetime.date.today()
            count_for_output = reported or cp_count

            if last_shown is None:
                # Detail page didn't expose "Last shown" — degraded path: at
                # least we know an advertiser entity exists → call 1B.
                return {"live": False, "ad_count": count_for_output,
                        "source": "google", "error": "no_last_shown",
                        "ever_advertised": True,
                        "ad_count_all_time": count_for_output,
                        "ad_count_30d": 0,
                        "last_shown": None}

            # Active = ad shown today or yesterday (TC updates ~daily)
            days_old = (today - last_shown).days
            live_now = days_old <= 2

            return {
                "live": live_now,
                "ad_count": count_for_output,
                "source": "google",
                "error": None if live_now else "stale",
                "ever_advertised": True,
                "ad_count_all_time": count_for_output,
                "ad_count_30d": count_for_output if live_now else 0,
                "last_shown": last_shown.isoformat(),
                "days_since_last_shown": days_old,
            }
        finally:
            try:
                page.close()
            except Exception:
                pass

    # --- Public API ------------------------------------------------------

    def verify(self, domain: str, business_name: str = "") -> dict:
        d = _domain_only(domain)
        if not d:
            return {"live": False, "ad_count": 0, "source": "google", "error": "no_url"}

        with self._cache_lock:
            cached = self._cache.get(d)
        if cached is not None:
            return cached

        try:
            self._ensure_pool()
        except Exception as e:
            return {"live": False, "ad_count": 0, "source": "google", "error": f"setup: {e}"}

        reply_q: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((d, reply_q))
        try:
            result = reply_q.get(timeout=(self.timeout_ms / 1000.0) * 3 + 10)
        except queue.Empty:
            result = {"live": False, "ad_count": 0, "source": "google", "error": "worker_timeout"}

        with self._cache_lock:
            self._cache[d] = result
        return result

    def close(self):
        workers = list(self._workers)
        if not workers:
            return
        for _ in workers:
            try:
                self._jobs.put(_SHUTDOWN)
            except Exception:
                pass
        for w in workers:
            try:
                w.join(timeout=5)
            except Exception:
                pass

    def __del__(self):
        self.close()


def _safe_parse_rpc(body: str) -> dict:
    if not body:
        return {}
    stripped = body.lstrip()
    if stripped.startswith(")]}'"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[4:]
    try:
        return json.loads(stripped)
    except Exception:
        return {}


def _count_creatives(payloads: list[dict]) -> int:
    total = 0
    seen_ids: set[str] = set()

    for p in payloads:
        if not isinstance(p, dict):
            continue
        bucket = p.get("1") if isinstance(p.get("1"), list) else None
        if bucket:
            for item in bucket:
                cid = _extract_creative_id(item)
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    total += 1
            continue
        for cid in _walk_creative_ids(p):
            if cid not in seen_ids:
                seen_ids.add(cid)
                total += 1

    return total


def _walk_advertiser_ids(node):
    """Yield advertiser IDs (pattern 'AR' + 19 digits) from an RPC payload."""
    import re as _re
    pat = _re.compile(r"\bAR\d{15,20}\b")
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            for m in pat.finditer(cur):
                yield m.group(0)


def _extract_creative_id(item) -> Optional[str]:
    if isinstance(item, dict):
        for key in ("2", "creativeId", "id"):
            v = item.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _walk_creative_ids(node):
    if isinstance(node, dict):
        cid = _extract_creative_id(node)
        if cid:
            yield cid
        for v in node.values():
            yield from _walk_creative_ids(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_creative_ids(v)
