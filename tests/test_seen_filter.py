import os, sys
# Ensure repo root is on sys.path so 'utils' is importable during pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sqlite3, json, tempfile, datetime

from utils.seen_filter import SeenFilter

def setup_db(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE Job_Listings (
            site_id TEXT,
            listing_id TEXT,
            keyword_id INTEGER,
            captured_at TEXT
        );
    """)
    # Seed existing data
    cur.executemany(
        "INSERT INTO Job_Listings(site_id, listing_id, keyword_id, captured_at) VALUES (?,?,?,?)",
        [
            ("SEEK", "A100", 1, "2025-08-01T00:00:00"),
            ("SEEK", "A101", 2, "2025-08-01T00:00:00"),
            ("SEEK", "A102", 2, "2025-08-01T00:00:00"),
        ],
    )
    conn.commit()

def test_seen_skips_and_counts():
    conn = sqlite3.connect(":memory:")
    setup_db(conn)
    sf = SeenFilter(conn=conn)
    sf.preload("SEEK")

    # Existing IDs -> should skip
    assert sf.is_seen("SEEK", "A100", keyword_id=1) is True
    assert sf.is_seen("SEEK", "A101", keyword_id=1) is True

    # New ID -> should not skip, then mark seen
    assert sf.is_seen("SEEK", "A200", keyword_id=2) is False
    sf.mark_seen("SEEK", "A200", keyword_id=2)
    # Second time should skip
    assert sf.is_seen("SEEK", "A200", keyword_id=2) is True

    s = sf.summary_dict("SEEK")
    assert s["skipped_existing"] >= 3
    assert s["new"] == 1

def test_overlap_tracking():
    conn = sqlite3.connect(":memory:")
    setup_db(conn)
    sf = SeenFilter(conn=conn)
    sf.preload("SEEK")
    # A101 exists under keyword 2, but we search with keyword 2 -> same keyword overlap
    sf.is_seen("SEEK", "A101", keyword_id=2)
    # A102 exists under keyword 2, but we search with keyword 3 -> other keyword overlap
    sf.is_seen("SEEK", "A102", keyword_id=3)
    s = sf.summary_dict("SEEK")
    assert s["overlap_same_keyword"] >= 1
    assert s["overlap_other_keyword"] >= 1