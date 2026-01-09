# dashboard/api_admin.py
import os, sqlite3
from flask import Blueprint, request, jsonify, abort

api_admin = Blueprint("api_admin", __name__, url_prefix="/api/admin")

DB_PATH = os.getenv("JOBHUNTER_DB", os.path.abspath("job_hunt.db"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "dev")  # change in prod

def _check_token():
    if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        abort(401, description="Invalid admin token")

def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'

def _fk_edges(con, tables):
    edges = {t: set() for t in tables}
    for t in tables:
        try:
            for row in con.execute(f"PRAGMA foreign_key_list({qident(t)})"):
                ref = row[2]  # referenced table name
                if ref in edges:  # edge only within target set
                    edges[t].add(ref)
        except sqlite3.OperationalError:
            pass
    return edges

def _toposort(tables, edges):
    # Kahn's algorithm on reversed edges (delete children first)
    incoming = {t: set() for t in tables}
    for t, refs in edges.items():
        for r in refs:
            incoming[r].add(t)
    order, roots = [], [t for t in tables if not incoming[t]]
    while roots:
        n = roots.pop()
        order.append(n)
        for m in list(incoming):
            if n in incoming[m]:
                incoming[m].remove(n)
                if not incoming[m]:
                    roots.append(m)
        incoming.pop(n, None)
    # If cycle remains, fall back to given order
    return order if not incoming else tables

@api_admin.post("/purge-data")
def purge_data():
    _check_token()

    dry_run = request.args.get("dry_run", "true").lower() == "true"
    vacuum  = request.args.get("vacuum", "false").lower() == "true"
    site_id = request.args.get("site_id")  # None means wipe all rows

    # Guard rails: if you’re using -1 for test data, refuse non -1 destructive ops unless you know what you’re doing
    if site_id is not None and not dry_run and str(site_id) != "-1":
        abort(400, description="Refusing to purge site_id != -1 when dry_run=false")

    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON;")

        # discover target tables
        data_tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'data_%'"
        )]

        # determine delete order via foreign keys (children first)
        edges = _fk_edges(con, data_tables)
        delete_order = list(reversed(_toposort(data_tables, edges)))  # children -> parents

        # count rows per table (with optional site filter)
        per_table, total = [], 0
        for t in data_tables:
            if site_id is None:
                sql = f"SELECT COUNT(*) FROM {qident(t)}"
                params = ()
            else:
                # only delete rows that have site_id when present; ignore tables lacking the column
                has_site = any(r[1] == "site_id" for r in con.execute(f"PRAGMA table_info({qident(t)})"))
                sql = f"SELECT COUNT(*) FROM {qident(t)} WHERE site_id = ?" if has_site else f"SELECT 0"
                params = (int(site_id),) if has_site else ()
            c = con.execute(sql, params).fetchone()[0]
            per_table.append({"table": t, "rows": c})
            total += c

        if dry_run:
            return jsonify({
                "db": DB_PATH,
                "scope": "tables name LIKE 'data_%'",
                "site_id": site_id,
                "dry_run": True,
                "tables": per_table,
                "total_rows": total,
                "delete_order": delete_order
            })

        # perform delete
        con.execute("BEGIN IMMEDIATE;")
        try:
            for t in delete_order:
                if site_id is None:
                    con.execute(f"DELETE FROM {qident(t)}")
                else:
                    has_site = any(r[1] == "site_id" for r in con.execute(f"PRAGMA table_info({qident(t)})"))
                    if has_site:
                        con.execute(f"DELETE FROM {qident(t)} WHERE site_id = ?", (int(site_id),))
            con.commit()
        except Exception as e:
            con.rollback()
            abort(500, description=f"Delete failed: {e}")

    if vacuum:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("VACUUM")

    return jsonify({
        "db": DB_PATH,
        "scope": "tables name LIKE 'data_%'",
        "site_id": site_id,
        "dry_run": False,
        "tables": per_table,
        "total_rows": total
    })
