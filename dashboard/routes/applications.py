# dashboard/routes/applications.py
# Provides the /applications summary view
from __future__ import annotations
from flask import Blueprint, render_template, request
import sqlite3
import os

# GPT-ANCHOR:start:imports_and_config
DB_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "job_hunt.db"))
applications_bp = Blueprint("applications", __name__)
# GPT-ANCHOR:end:imports_and_config

def _connect():
    return sqlite3.connect(DB_FILE)

# GPT-ANCHOR:start:route_applications
@applications_bp.route("/applications")
def applications():
    user = request.args.get("user", "").strip()
    site = request.args.get("site", "").strip()
    added_since = request.args.get("added_since", "").strip()  # e.g. '7d' or '30d'

    # Build WHERE clauses
    where = ["status IN ('applied','pending','ignored')"]
    params = []
    if user:
        where.append("EXISTS (SELECT 1 FROM Roles r JOIN Keywords k ON r.role_id=k.role_id WHERE k.keyword_id=jl.keyword_id AND r.user_id=?)")
        params.append(user)
    if site:
        where.append("jl.site_id = ?")
        params.append(site)
    if added_since.endswith("d") and added_since[:-1].isdigit():
        days = int(added_since[:-1])
        where.append("jl.captured_at >= datetime('now', ?)")
        params.append(f"-{days} days")
    where_sql = " AND ".join(where)

    # Summary aggregates
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) AS applied_cnt,
            SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_cnt,
            SUM(CASE WHEN status='ignored' THEN 1 ELSE 0 END) AS ignored_cnt,
            COUNT(*) AS total_cnt
        FROM Job_Listings jl
        WHERE {where_sql}
    """, params)
    row = cur.fetchone() or (0,0,0,0)
    summary = {
        "applied": row[0] or 0,
        "pending": row[1] or 0,
        "ignored": row[2] or 0,
        "total": row[3] or 0,
    }

    # Recent actions list
    cur.execute(f"""
        SELECT jl.listing_id, jl.title, jl.company, jl.location, jl.status, jl.suitability_score, jl.captured_at
        FROM Job_Listings jl
        WHERE {where_sql}
        ORDER BY jl.captured_at DESC
        LIMIT 200
    """, params)
    items = [
        {
            "listing_id": lid,
            "title": title,
            "company": company,
            "location": location,
            "status": status,
            "score": score,
            "captured_at": captured_at,
        }
        for (lid, title, company, location, status, score, captured_at) in cur.fetchall()
    ]

    conn.close()
    return render_template("applications.html",
                           summary=summary,
                           items=items,
                           user=user,
                           site=site,
                           added_since=added_since)
# GPT-ANCHOR:end:route_applications
