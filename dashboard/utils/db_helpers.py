import os
from datetime import datetime
import sqlite3

DB_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "job_hunt.db")
)

def get_roles(scanned_since=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_id, role_name, enabled FROM Roles ORDER BY rank ASC")
    roles = []
    totals = {"not": 0, "possible": 0, "high": 0, "total": 0}
    for role_id, role_name, enabled in c.fetchall():
        c.execute(
            "SELECT keyword_id, keyword, enabled FROM Keywords WHERE role_id=? ORDER BY keyword",
            (role_id,),
        )
        keywords = []
        role_counts = {"not": 0, "possible": 0, "high": 0, "total": 0}
        for k_id, keyword, k_enabled in c.fetchall():
            query = """
                SELECT
                    SUM(CASE WHEN suitability_score = 1 THEN 1 ELSE 0 END) AS not_cnt,
                    SUM(CASE WHEN suitability_score = 3 THEN 1 ELSE 0 END) AS possible_cnt,
                    SUM(CASE WHEN suitability_score = 5 THEN 1 ELSE 0 END) AS high_cnt,
                    COUNT(*) AS total_cnt
                FROM Job_Listings
                WHERE keyword_id = ?
            """
            params = [k_id]
            if scanned_since:
                query += " AND captured_at >= ?"
                params.append(scanned_since)
            c.execute(query, params)
            not_cnt, possible_cnt, high_cnt, total_cnt = [x or 0 for x in c.fetchone()]
            counts = {
                "not": not_cnt,
                "possible": possible_cnt,
                "high": high_cnt,
                "total": total_cnt,
            }
            keywords.append(
                {
                    "keyword_id": k_id,
                    "keyword": keyword,
                    "enabled": k_enabled,
                    "counts": counts,
                }
            )
            for key in role_counts:
                role_counts[key] += counts[key]
        roles.append(
            {
                "role_id": role_id,
                "role_name": role_name,
                "enabled": enabled,
                "keywords": keywords,
                "counts": role_counts,
            }
        )
        for key in totals:
            totals[key] += role_counts[key]
    conn.close()
    return roles, totals

def toggle_role_enabled(role_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE Roles SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE role_id=?", (role_id,))
    conn.commit()
    conn.close()

def add_role(role_name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO Roles (role_name, enabled) VALUES (?, 1)", (role_name,))
    conn.commit()
    conn.close()

def toggle_keyword_enabled(keyword_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE Keywords SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE keyword_id=?", (keyword_id,))
    conn.commit()
    conn.close()

def add_keyword(keyword, role_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO Keywords (keyword, role_id, enabled) VALUES (?, ?, 1)", (keyword, role_id))
    conn.commit()
    conn.close()

def get_keywords():
    """Return a simple list of all keywords for the filter dropdown."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT keyword FROM Keywords WHERE enabled = 1 ORDER BY keyword")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_listings(keyword=None, suitability=None, role_id=None, scanned_since=None):
    """
    Fetch listings with optional filters:
      - keyword: exact keyword text match (on Keywords.keyword)
      - suitability: 'not' (<2), 'mid' (=2), 'high' (>=3)
      - role_id: restrict to a role’s keywords
      - scanned_since: ISO date (YYYY-MM-DD) or full timestamp; filters by captured_at
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    query = (
        "SELECT jl.listing_id, jl.title, jl.company, jl.location, jl.url, "
        "       jl.suitability_score, jl.status, jl.captured_at, k.keyword, k.role_id "
        "FROM Job_Listings jl "
        "JOIN Keywords k ON jl.keyword_id = k.keyword_id"
    )
    params = []
    conditions = []

    if keyword:
        conditions.append("k.keyword = ?")
        params.append(keyword)

    if role_id:
        conditions.append("k.role_id = ?")
        params.append(role_id)

    if suitability == "not":
        conditions.append("jl.suitability_score <= 2")
    elif suitability == "mid":
        conditions.append("jl.suitability_score = 3")
    elif suitability == "high":
        conditions.append("jl.suitability_score > 3")

    if scanned_since:
        # Accept 'YYYY-MM-DD' or a full timestamp; compare with SQLite datetime()
        conditions.append("datetime(jl.captured_at) >= datetime(?)")
        # If only date provided, normalize to start of day
        if len(scanned_since) == 10:
            scanned_since = scanned_since + " 00:00:00"
        params.append(scanned_since)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY datetime(jl.captured_at) DESC, jl.listing_id DESC"

    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    # Map to dicts for the template
    listings = []
    for lid, title, company, location, url, score, status, captured_at, kw, r_id in rows:
        listings.append({
            "listing_id": lid,
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "suitability_score": score,
            "status": status,
            "captured_at": captured_at,
            "keyword": kw,
            "role_id": r_id,
        })
    return listings

def update_listing_status(listing_id, status):
    """Update the status of a job listing."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE Job_Listings SET status=? WHERE listing_id=?",
        (status, listing_id),
    )
    conn.commit()
    conn.close()

