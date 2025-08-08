import os
import sqlite3

DB_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "job_hunt.db")
)

def get_roles():
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
            c.execute(
                """
                SELECT
                    SUM(CASE WHEN suitability_score = 1 THEN 1 ELSE 0 END) AS not_cnt,
                    SUM(CASE WHEN suitability_score = 3 THEN 1 ELSE 0 END) AS possible_cnt,
                    SUM(CASE WHEN suitability_score = 5 THEN 1 ELSE 0 END) AS high_cnt,
                    COUNT(*) AS total_cnt
                FROM Job_Listings
                WHERE keyword_id = ?
                """,
                (k_id,),
            )
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
    """Return a list of available keywords ordered alphabetically."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT keyword FROM Keywords ORDER BY keyword")
    keywords = [row[0] for row in c.fetchall()]
    conn.close()
    return keywords

def get_listings(keyword=None, suitability=None, role_id=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    query = (
        "SELECT jl.listing_id, jl.title, jl.company, jl.location, jl.url, "
        "jl.suitability_score, jl.status "
        "FROM Job_Listings jl "
        "JOIN Keywords k ON jl.keyword_id = k.keyword_id "
        "JOIN Roles r ON k.role_id = r.role_id"
    )
    params = []
    conditions = []

    if keyword:
        conditions.append("k.keyword = ?")
        params.append(keyword)

    if role_id:
        conditions.append("r.role_id = ?")
        params.append(role_id)

    if suitability == "not":
        conditions.append("jl.suitability_score < 2")
    elif suitability == "mid":
        conditions.append("jl.suitability_score = 2")
    elif suitability == "high":
        conditions.append("jl.suitability_score >= 3")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY jl.captured_at DESC"

    c.execute(query, params)
    rows = [
        {
            "listing_id": r[0],
            "title": r[1],
            "company": r[2],
            "location": r[3],
            "url": r[4],
            "suitability_score": r[5],
            "status": r[6],
        }
        for r in c.fetchall()
    ]
    conn.close()
    return rows

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

