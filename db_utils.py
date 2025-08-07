import sqlite3
DB_FILE = "job_hunt.db"

def get_active_keywords():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT k.keyword_id, k.keyword
        FROM Keywords k
        JOIN Roles r ON k.role_id = r.role_id
        WHERE r.enabled = 1 AND k.active=1
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def insert_job_listing(**job):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO Job_Listings(listing_id, keyword_id, title, company, location, url, listing_date, suitability_score)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["listing_id"], job["keyword_id"], job["title"], job["company"],
                job["location"], job["url"], job.get("listing_date"), job.get("suitability_score", 0)
            )
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

def insert_run_summary(keyword_id, listings_found, highly_suitable, applications_made=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO Search_Run_Summary(keyword_id, listings_found, highly_suitable, applications_made) VALUES (?,?,?,?)",
        (keyword_id, listings_found, highly_suitable, applications_made)
    )
    conn.commit()
    conn.close()
