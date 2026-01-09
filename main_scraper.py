from __future__ import annotations
"""
Main scraper orchestrator for Job Hunter (dev branch)

Key behaviors:
- PASS 1 (summary): build search URL, parse summary cards, SKIP already-seen listing_ids (REQ-003),
  and insert minimal rows only for truly new items.
- PASS 2 (deep): optional; visit detail pages for items below a threshold score, extract description,
  re-score via criteria, and persist enrichment + new score (shims provided if helpers are absent).
- Progress events are emitted to progress_bus.save_progress at key milestones.
- DB should also enforce uniqueness as a safety net (see migration).

This file is robust when executed directly:
- Keeps __future__ import at top (Python rule).
- Adds repo root to sys.path immediately after __future__ import.
- Supports either `adapters.get_adapter_for` or `scrapers.get_adapter_for` (shimmed).
"""

# -----------------------------
# Import path bootstrap
# -----------------------------
import os as _os, sys as _sys
_SCRIPT_DIR = _os.path.abspath(_os.path.dirname(__file__))
if _SCRIPT_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPT_DIR)
_REPO_PARENT = _os.path.abspath(_os.path.join(_SCRIPT_DIR, _os.pardir))
if _REPO_PARENT not in _sys.path:
    _sys.path.insert(0, _REPO_PARENT)

# Provide an alias module if the project exposes `scrapers` but not `adapters`
try:
    import types as _types
    try:
        import adapters as _adapters  # noqa: F401
    except ModuleNotFoundError:
        _adapters = None  # type: ignore
    if _adapters is None:
        try:
            import scrapers as _scrapers  # type: ignore
        except ModuleNotFoundError:
            _scrapers = None  # type: ignore
        if _scrapers is not None and "adapters" not in _sys.modules:
            _adapters_mod = _types.ModuleType("adapters")
            if hasattr(_scrapers, "get_adapter_for"):
                _adapters_mod.get_adapter_for = getattr(_scrapers, "get_adapter_for")
                _sys.modules["adapters"] = _adapters_mod
except Exception:
    # Non-fatal: if neither module exists, a clear ImportError will be raised at real import time.
    pass

# -----------------------------
# Standard / 3rd-party imports
# -----------------------------
import json
import time
import re
import sqlite3
import logging
from types import SimpleNamespace
from typing import Dict, Any, List, Tuple

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# -----------------------------
# Project-local imports & shims
# -----------------------------
import config
import db_utils  # used with shims below
try:
    from adapters import get_adapter_for  # type: ignore
except Exception as _e:
    raise ImportError("Could not import get_adapter_for from adapters (or scrapers shim).") from _e

# progress_bus shim (fallback to stdout JSON if module not present)
try:
    from progress_bus import save_progress
except ModuleNotFoundError:
    def save_progress(payload: dict) -> None:
        try:
            print(json.dumps({"event": "progress", "payload": payload}, ensure_ascii=False))
        except Exception:
            pass

from utils.seen_filter import SeenFilter

# -----------------------------
# Logging configuration
# -----------------------------
_DEFAULT_LOG_FILE = _os.path.join(_SCRIPT_DIR, "logs", "job_hunter.log")
_LOG_FILE = getattr(config, "LOG_FILE", _DEFAULT_LOG_FILE)
_os.makedirs(_os.path.dirname(_LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=getattr(config, "LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()  # console too, so you can see milestones live
    ],
)
log = logging.getLogger("job_hunter")


# =====================================================================================
# db_utils SHIMS (so this file works with your current minimal db_utils.py)
# =====================================================================================

def load_sites(conn) -> List[SimpleNamespace]:
    """
    Fallback loader for site configs when db_utils.load_sites is unavailable.
    Ensures returned objects have at least: site_id, site_name, url (required by adapters).
    """
    if hasattr(db_utils, "load_sites"):
        try:
            sites = db_utils.load_sites(conn)  # type: ignore[attr-defined]
            # Ensure each site has a url attribute
            for s in sites:
                if not hasattr(s, "url") or not getattr(s, "url"):
                    # Try adapter default, config, or safe default
                    try:
                        adapter = get_adapter_for(s.site_name)
                        base_url = getattr(adapter, "base_url", "")
                    except Exception:
                        base_url = ""
                    if not base_url:
                        base_url = getattr(config, "DEFAULT_SITE_URL", "") or "https://www.seek.com.au"
                    setattr(s, "url", base_url)
            return sites
        except Exception as e:
            log.warning("load_sites failed in db_utils; using fallback. err=%s", e)

    default_name = getattr(config, "DEFAULT_SITE_NAME", "DEFAULT")
    default_url = getattr(config, "DEFAULT_SITE_URL", "") or "https://www.seek.com.au"
    return [SimpleNamespace(site_id=1, site_name=default_name, url=default_url)]


def active_keywords_for_user(conn, user_id: int) -> List[Tuple[int, str]]:
    """
    Fallback to db_utils.get_active_keywords() which returns (keyword_id, keyword).
    """
    if hasattr(db_utils, "active_keywords_for_user"):
        try:
            return db_utils.active_keywords_for_user(conn, user_id)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("active_keywords_for_user failed in db_utils; trying get_active_keywords. err=%s", e)
    try:
        return db_utils.get_active_keywords()  # type: ignore[attr-defined]
    except Exception as e:
        log.warning("get_active_keywords not available; using empty list. err=%s", e)
        return []


def write_run_summary(conn, keyword_id: int, site_id: int, *, listings_found: int,
                      skipped_duplicates: int = 0, highly_suitable: int = 0,
                      applications_made: int = 0) -> None:
    """
    Fallback maps to db_utils.insert_run_summary(...) with available columns.
    """
    if hasattr(db_utils, "write_run_summary"):
        try:
            return db_utils.write_run_summary(conn, keyword_id, site_id, listings_found,
                                              skipped_duplicates=skipped_duplicates,
                                              highly_suitable=highly_suitable,
                                              applications_made=applications_made)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("write_run_summary failed in db_utils; trying insert_run_summary. err=%s", e)
    try:
        return db_utils.insert_run_summary(keyword_id, listings_found, highly_suitable, applications_made)  # type: ignore[attr-defined]
    except Exception as e:
        log.warning("insert_run_summary not available; skipping. err=%s", e)
        return None


def recalc_bucket_counts(conn, site_id: int, keyword_id: int) -> Dict[str, int]:
    """
    Minimal fallback. If you later add a real implementation in db_utils, this will auto-upgrade.
    """
    if hasattr(db_utils, "recalc_bucket_counts"):
        try:
            return db_utils.recalc_bucket_counts(conn, site_id, keyword_id)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("recalc_bucket_counts failed in db_utils; defaulting to zeros. err=%s", e)
    return {"not": 0, "mid": 0, "high": 0}


def select_for_deep_scan(conn, site_id: int, keyword_id: int, threshold: int, limit=None) -> List[Tuple[str, str]]:
    """
    Minimal fallback: no deep-scan candidates.
    """
    if hasattr(db_utils, "select_for_deep_scan"):
        try:
            return db_utils.select_for_deep_scan(conn, site_id, keyword_id, threshold, limit)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("select_for_deep_scan failed in db_utils; defaulting to empty. err=%s", e)
    return []


def load_criteria(conn, user_id: int):
    """
    Minimal fallback: no criteria.
    """
    if hasattr(db_utils, "load_criteria"):
        try:
            return db_utils.load_criteria(conn, user_id)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("load_criteria failed in db_utils; defaulting to empty. err=%s", e)
    return []


def apply_criteria_score(base_score: int, criteria, listing: Dict[str, Any]) -> int:
    """
    Minimal fallback: pass-through score.
    """
    if hasattr(db_utils, "apply_criteria_score"):
        try:
            return db_utils.apply_criteria_score(base_score, criteria, listing)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("apply_criteria_score failed in db_utils; using base score. err=%s", e)
    return base_score


def update_listing_enrichment(conn, listing_id: str, enrichment: Dict[str, Any], new_score: int) -> None:
    """
    Minimal fallback: update only suitability_score if the column exists; ignore enrichment if schema doesn't support it.
    """
    if hasattr(db_utils, "update_listing_enrichment"):
        try:
            return db_utils.update_listing_enrichment(conn, listing_id, enrichment, new_score)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("update_listing_enrichment failed in db_utils; attempting minimal update. err=%s", e)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE Job_Listings SET suitability_score = ? WHERE listing_id = ?", (new_score, listing_id))
        conn.commit()
    except Exception as e:
        log.warning("Minimal enrichment update failed; skipping. err=%s", e)


# =====================================================================================
# Driver construction
# =====================================================================================

def make_driver() -> webdriver.Chrome:
    """
    Construct and return a configured Selenium Chrome WebDriver.
    """
    chrome_options = Options()
    if getattr(config, "HEADLESS", True):
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1280,1800")
    chrome_bin = getattr(config, "CHROME_BINARY", None)
    if chrome_bin:
        chrome_options.binary_location = chrome_bin

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(getattr(config, "PAGE_LOAD_TIMEOUT_SEC", 30))
    return driver


# =====================================================================================
# PASS 1: Summary scrape
# =====================================================================================

def scrape_site_summary(
    driver: webdriver.Chrome,
    site_cfg,
    adapter,
    keyword_id: int,
    keyword: str,
    user_id: int,
    idx: int,
    total_keywords: int,
    *,
    seen: SeenFilter,
    db_path: str,
) -> Tuple[int, int]:
    """
    PASS 1: Perform summary scrape (listing cards). Skip already-seen listing_ids on sight (REQ-003).
    Insert minimal rows only for new items and return (inserted_count, total_reported_by_site).
    """
    inserted = 0
    total_reported = 0

    # Some adapters require a 'page' parameter; call with page=1 when supported,
    # and gracefully fall back to legacy signature otherwise.
    try:
        url = adapter.build_search_url(site_cfg, keyword, page=1)
    except TypeError:
        url = adapter.build_search_url(site_cfg, keyword)

    log.info(
        "PASS 1 | site=%s id=%s | keyword[%d/%d]='%s' | GET %s",
        site_cfg.site_name, site_cfg.site_id, idx, total_keywords, keyword, url,
    )

    driver.get(url)
    time.sleep(getattr(config, "WAIT_SEARCH_SEC", 2))
    html = driver.page_source

    soup = BeautifulSoup(html, "html.parser")
    cards = adapter.parse_summary_cards(soup)
    total_reported = adapter.parse_total_results(soup) or len(cards)
    log.info(
        "PASS 1 | site=%s | keyword='%s' | reported=%s cards=%s",
        site_cfg.site_name, keyword, total_reported, len(cards),
    )

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        for card in cards:
            listing_id = card.get("listing_id") or card.get("id")
            if not listing_id:
                continue

            # REQ-003: fast skip for already-seen listing_ids
            if seen.is_seen(site_id=site_cfg.site_id, listing_id=listing_id, keyword_id=keyword_id):
                continue

            ok = False
            if hasattr(adapter, "insert_minimal_listing"):
                try:
                    ok = bool(
                        adapter.insert_minimal_listing(
                            conn,
                            user_id=user_id,
                            site_id=site_cfg.site_id,
                            keyword_id=keyword_id,
                            listing_id=listing_id,
                            title=card.get("title") or "",
                            company=card.get("company") or "",
                            location=card.get("location") or "",
                            url=card.get("url") or "",
                            base_score=getattr(config, "BASE_ENTRY_SCORE", 0),
                        )
                    )
                except Exception as e:
                    log.warning("insert_minimal_listing failed; trying db_utils fallback. err=%s", e)

            if not ok and hasattr(db_utils, "insert_job_listing"):
                try:
                    db_utils.insert_job_listing(
                        listing_id=listing_id,
                        keyword_id=keyword_id,
                        title=card.get("title") or "",
                        company=card.get("company") or "",
                        location=card.get("location") or "",
                        url=card.get("url") or "",
                        listing_date=None,
                        suitability_score=getattr(config, "BASE_ENTRY_SCORE", 0),
                    )
                    ok = True
                except Exception as e:
                    log.warning("db_utils.insert_job_listing failed; skipping. err=%s", e)

            if ok:
                inserted += 1
                seen.mark_seen(site_id=site_cfg.site_id, listing_id=listing_id, keyword_id=keyword_id)

    log.info(
        "PASS 1 DONE | site=%s | keyword='%s' | inserted(new)=%s / reported=%s",
        site_cfg.site_name, keyword, inserted, total_reported,
    )
    return inserted, total_reported


# =====================================================================================
# Main Orchestration
# =====================================================================================

def main() -> None:
    t0 = time.time()
    log.info("=== Job Hunter START === (log: %s)", _LOG_FILE)

    # Create driver
    try:
        driver = make_driver()
        log.info("Driver created (HEADLESS=%s)", getattr(config, "HEADLESS", True))
    except Exception:
        log.exception("Failed to create Selenium driver")
        raise

    try:
        db_path = getattr(config, "DB_PATH", getattr(db_utils, "DB_FILE", "job_hunt.db"))
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            log.info("DB connected: %s", db_path)

            # REQ-003: prepare seen-filter for skip-on-sight (used in PASS 1 and for metrics)
            seen = SeenFilter(db_path=db_path)

            # Load sites (optionally filter by SITES_INCLUDE)
            all_sites = load_sites(conn)
            if getattr(config, "SITES_INCLUDE", None):
                sites = [s for s in all_sites if s.site_name in config.SITES_INCLUDE]
            else:
                sites = all_sites

            # Load active keywords for the current user
            keywords = active_keywords_for_user(conn, getattr(config, "USER_ID", 1))
            klimit = getattr(config, "KEYWORD_LIMIT_PER_SITE", None)
            if klimit:
                keywords = keywords[: int(klimit)]
            total_keywords = len(keywords)

            # Preload criteria for card-view scoring
            criteria = load_criteria(conn, getattr(config, "USER_ID", 1))

            log.info("Context ready: sites=%d keywords=%d", len(sites), total_keywords)

            # Loop sites
            for site_cfg in sites:
                # Ensure site_cfg.url exists (adapters require it); set from adapter or config if missing.
                if not hasattr(site_cfg, "url") or not getattr(site_cfg, "url"):
                    try:
                        _adapter = get_adapter_for(site_cfg.site_name)
                        site_cfg.url = getattr(_adapter, "base_url", "")
                    except Exception:
                        site_cfg.url = ""
                    if not site_cfg.url:
                        site_cfg.url = getattr(config, "DEFAULT_SITE_URL", "") or "https://www.seek.com.au"

                log.info("SITE START: %s (id=%s)", site_cfg.site_name, site_cfg.site_id)
                seen.preload(site_cfg.site_id)
                adapter = get_adapter_for(site_cfg.site_name)
                site_label = site_cfg.site_name

                # Loop keywords
                for idx, (keyword_id, keyword) in enumerate(keywords, start=1):
                    # Initial progress reset per keyword (PASS 1)
                    save_progress({
                        "site": site_label,
                        "phase": "PASS 1",
                        "keyword": keyword,
                        "keyword_index": idx,
                        "total_keywords": total_keywords,
                        "processed_count": 0,
                        "total_listings": 0,
                        "not_suitable": 0,
                        "suitable": 0,
                        "highly_suitable": 0,
                        "skipped_existing": 0,
                        "deep_scanned": 0,
                        "total_deep": 0,
                    })

                    inserted, total_reported = scrape_site_summary(
                        driver,
                        site_cfg,
                        adapter,
                        keyword_id,
                        keyword,
                        getattr(config, "USER_ID", 1),
                        idx,
                        total_keywords,
                        seen=seen,
                        db_path=db_path,
                    )

                    # Update counts & run summary
                    buckets = recalc_bucket_counts(conn, site_cfg.site_id, keyword_id)
                    write_run_summary(
                        conn,
                        keyword_id,
                        site_cfg.site_id,
                        listings_found=total_reported,
                        skipped_duplicates=max(total_reported - inserted, 0),
                    )

                    save_progress({
                        "site": site_label,
                        "phase": "PASS 1",
                        "keyword": keyword,
                        "keyword_index": idx,
                        "total_keywords": total_keywords,
                        "processed_count": inserted,
                        "total_listings": total_reported,
                        "not_suitable": buckets["not"],
                        "suitable": buckets["mid"],
                        "highly_suitable": buckets["high"],
                        "skipped_existing": max(total_reported - inserted, 0),
                        "deep_scanned": 0,
                        "total_deep": 0,
                    })

                    # PASS 2: deep scan (optional)
                    if getattr(config, "ENABLE_DEEP_SCAN", False) and getattr(config, "DEEP_SCAN_THRESHOLD", None) is not None:
                        to_scan = select_for_deep_scan(
                            conn,
                            site_cfg.site_id,
                            keyword_id,
                            threshold=config.DEEP_SCAN_THRESHOLD,
                            limit=getattr(config, "DEEP_SCAN_LIMIT_PER_KEYWORD", None),
                        )
                        total_deep = len(to_scan)
                        deep_done = 0
                        log.info("PASS 2 START | site=%s | keyword='%s' | to_scan=%s",
                                 site_label, keyword, total_deep)

                        for (listing_id, url) in to_scan:
                            save_progress({
                                "site": site_label,
                                "phase": "PASS 2",
                                "keyword": keyword,
                                "keyword_index": idx,
                                "total_keywords": total_keywords,
                                "processed_count": inserted,
                                "total_listings": total_reported,
                                "not_suitable": buckets["not"],
                                "suitable": buckets["mid"],
                                "highly_suitable": buckets["high"],
                                "skipped_existing": max(total_reported - inserted, 0),
                                "deep_scanned": deep_done,
                                "total_deep": total_deep,
                            })

                            # Navigate to detail page and enrich
                            driver.get(url)
                            time.sleep(getattr(config, "WAIT_JOB_SEC", 2))
                            html = driver.page_source
                            enrichment = getattr(adapter, "deep_enrich", lambda *a, **k: {})(site_cfg, html)

                            # Pull minimal fields we may have stored already
                            c = conn.cursor()
                            c.execute(
                                """
                                SELECT title, location, suitability_score
                                FROM Job_Listings
                                WHERE listing_id = ?
                                """,
                                (listing_id,),
                            )
                            row = c.fetchone() or ("", "", getattr(config, "BASE_ENTRY_SCORE", 0))

                            # Build a dict with fields for criteria matching
                            listing_for_scoring = {
                                "title": row[0] or "",
                                "location": row[1] or "",
                                "description": enrichment.get("description", ""),
                            }

                            # Apply criteria scoring
                            base_score = row[2] or getattr(config, "BASE_ENTRY_SCORE", 0)
                            new_score = apply_criteria_score(base_score, criteria, listing_for_scoring)

                            # Update DB with enrichment + new score
                            update_listing_enrichment(conn, listing_id, enrichment, new_score)

                            # Refresh buckets occasionally
                            deep_done += 1
                            if deep_done % 5 == 0 or deep_done == total_deep:
                                buckets = recalc_bucket_counts(conn, site_cfg.site_id, keyword_id)
                                save_progress({
                                    "site": site_label,
                                    "phase": "PASS 2",
                                    "keyword": keyword,
                                    "keyword_index": idx,
                                    "total_keywords": total_keywords,
                                    "processed_count": inserted,
                                    "total_listings": total_reported,
                                    "not_suitable": buckets["not"],
                                    "suitable": buckets["mid"],
                                    "highly_suitable": buckets["high"],
                                    "skipped_existing": max(total_reported - inserted, 0),
                                    "deep_scanned": deep_done,
                                    "total_deep": total_deep,
                                })

                        log.info("PASS 2 DONE | site=%s | keyword='%s' | processed=%s",
                                 site_label, keyword, deep_done)

                log.info("SITE DONE: %s (id=%s)", site_cfg.site_name, site_cfg.site_id)

    except Exception:
        log.exception("Unhandled exception in main()")
        raise
    finally:
        try:
            driver.quit()
            log.info("Driver closed")
        except Exception:
            log.exception("Error while closing driver")
        elapsed = time.time() - t0
        log.info("=== Job Hunter END (%.2fs) ===", elapsed)


if __name__ == "__main__":
    main()
