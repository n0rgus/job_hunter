import sqlite3
import os

DB_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "job_hunt.db")
)

def get_roles():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role_id, role_name, enabled FROM Roles ORDER BY role_name")
    roles = []
    for role_id, role_name, enabled in c.fetchall():
        c.execute("SELECT keyword_id, keyword, enabled FROM Keywords WHERE role_id=? ORDER BY keyword", (role_id,))
        keywords = [{"keyword_id": k[0], "keyword": k[1], "enabled": k[2]} for k in c.fetchall()]
        roles.append({"role_id": role_id, "role_name": role_name, "enabled": enabled, "keywords": keywords})
    conn.close()
    return roles

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
