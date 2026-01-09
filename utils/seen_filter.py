"""
utils/seen_filter.py
--------------------
Drop-in helper to satisfy REQ-003: skip previously seen listing_id's on sight.

Usage (example integration in main_scraper.py):
    from utils.seen_filter import SeenFilter

    seen = SeenFilter(db_path=config.DB_PATH)   # or pass an open sqlite3.Connection via conn=...
    seen.preload(site_id="SEEK")                # load existing listing_ids for the site into memory

    for item in items_from_adapter:
        listing_id = item.get("listing_id") or item.get("id")
        keyword_id = item.get("keyword_id") or current_keyword_id
        if seen.is_seen(site_id="SEEK", listing_id=listing_id, keyword_id=keyword_id):
            # already known -> skip without fetching details
            continue

        # ... fetch detail page, enrich, and insert into DB ...

        seen.mark_seen(site_id="SEEK", listing_id=listing_id, keyword_id=keyword_id)

    # persist or print counters after the run
    print(seen.summary_str(site_id="SEEK"))
    seen.append_run_metrics(file_path="metrics/seen_counts.jsonl",
                            site_id="SEEK", extra={"keyword_id": current_keyword_id})

Notes:
  * This module assumes a SQLite table: Job_Listings(site_id TEXT, listing_id TEXT, keyword_id INTEGER, captured_at TEXT, ...)
  * For best performance, an index or UNIQUE constraint on (site_id, listing_id) is recommended.
"""
from __future__ import annotations
import os, json, sqlite3, datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Any

@dataclass
class SiteSeenState:
    loaded: bool = False
    listing_ids: Set[str] = field(default_factory=set)
    # Counters
    new_count: int = 0
    skipped_existing: int = 0
    overlap_same_keyword: int = 0
    overlap_other_keyword: int = 0

class SeenFilter:
    def __init__(self, db_path: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> None:
        if conn is None and not db_path:
            raise ValueError("Provide either db_path or conn")
        self._db_path = db_path
        self._conn = conn
        self._sites: Dict[str, SiteSeenState] = {}

    # --- Internal connection helper
    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        assert self._db_path is not None
        # regular rw connection (caller writes inserts); we only read
        return sqlite3.connect(self._db_path)

    # --- Load known listing_ids for a site into memory
    def preload(self, site_id: str) -> None:
        site = self._sites.setdefault(site_id, SiteSeenState())
        if site.loaded:
            return
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT listing_id FROM Job_Listings WHERE site_id = ?", (site_id,))
            rows = cur.fetchall()
            site.listing_ids = {str(r[0]) for r in rows if r and r[0] is not None}
            site.loaded = True
        finally:
            if self._conn is None:
                conn.close()

    # --- Check if seen
    def is_seen(self, site_id: str, listing_id: Optional[str], keyword_id: Optional[int] = None) -> bool:
        if not listing_id:
            return False  # no id -> cannot de-dup at this stage
        self.preload(site_id)
        site = self._sites[site_id]
        lid = str(listing_id)
        if lid in site.listing_ids:
            site.skipped_existing += 1
            # Optional overlap tracking by keyword_id
            if keyword_id is not None:
                self._update_overlap(site_id, lid, keyword_id)
            return True
        return False

    # --- Mark a listing as newly seen (after successful insert into DB)
    def mark_seen(self, site_id: str, listing_id: Optional[str], keyword_id: Optional[int] = None) -> None:
        if not listing_id:
            return
        self.preload(site_id)
        site = self._sites[site_id]
        lid = str(listing_id)
        if lid not in site.listing_ids:
            site.listing_ids.add(lid)
            site.new_count += 1

    # --- Optional: determine if the existing row(s) matched the same keyword or other keywords
    def _update_overlap(self, site_id: str, listing_id: str, incoming_keyword_id: int) -> None:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT keyword_id FROM Job_Listings WHERE site_id = ? AND listing_id = ?",
                (site_id, listing_id),
            )
            rows = [r[0] for r in cur.fetchall() if r and r[0] is not None]
            if not rows:
                return
            if any(int(k) == int(incoming_keyword_id) for k in rows):
                self._sites[site_id].overlap_same_keyword += 1
            else:
                self._sites[site_id].overlap_other_keyword += 1
        finally:
            if self._conn is None:
                conn.close()

    # --- Metrics & export
    def summary_dict(self, site_id: str) -> Dict[str, Any]:
        self.preload(site_id)
        s = self._sites[site_id]
        return {
            "site_id": site_id,
            "new": s.new_count,
            "skipped_existing": s.skipped_existing,
            "overlap_same_keyword": s.overlap_same_keyword,
            "overlap_other_keyword": s.overlap_other_keyword,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }

    def summary_str(self, site_id: str) -> str:
        d = self.summary_dict(site_id)
        return (
            f"[{d['site_id']}] new={d['new']} "
            f"skipped_existing={d['skipped_existing']} "
            f"overlap_same_keyword={d['overlap_same_keyword']} "
            f"overlap_other_keyword={d['overlap_other_keyword']}"
        )

    def append_run_metrics(self, file_path: str, site_id: str, extra: Optional[Dict[str, Any]] = None) -> None:
        d = self.summary_dict(site_id)
        if extra:
            d.update(extra)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")