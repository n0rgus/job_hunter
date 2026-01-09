#!/usr/bin/env python3
"""
Migration: rename legacy tables to prefixed names (system_, config_, data_).

What it does
------------
- Creates a timestamped .bak backup of the DB (unless --dry-run)
- Renames old tables -> new names (idempotent: only if old exists & new doesn't)
- Optionally creates compatibility VIEWS with the *old* names pointing to the new tables
- Optionally adds indexes on site_id for data_* tables
- Runs PRAGMA foreign_key_check after migration and reports violations

Usage
-----
Dry run:
    python migrate_names.py --db D:\Data\Code\Repos\job_hunter-dev\job_hunt.db --dry-run

Apply changes (+compat views +site_id indexes):
    python migrate_names.py --db D:\Data\Code\Repos\job_hunter-dev\job_hunt.db

Skip views or indexes:
    python migrate_names.py --db job_hunt.db --no-views --no-indexes
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# Old -> New mapping (quote old names that include spaces when executing SQL)
MAPPING = {
    "Users": "system_users",
    "Sites": "config_sites",
    "Keywords": "data_keywords",
    "Applications": "data_applications",
    "Roles": "data_roles",
    "Criteria": "config_criteria",
    "Criteria Lists": "config_criteria_lists",
    "Job_Listings": "data_job_listings",
    "Search_Run_Summary": "data_search_run_summary",
}

DATA_PREFIX = "data_"


def qident(name: str) -> str:
    """Safely quote a SQLite identifier (table/view/index/column)."""
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_version(con: sqlite3.Connection) -> str:
    return con.execute("SELECT sqlite_version()").fetchone()[0]


def backup_db(db_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{db_path}.{ts}.bak"
    shutil.copy2(db_path, bak)
    return bak


def obj_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE (type='table' OR type='view') AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def is_view(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return bool(row and row[0] == "view")


def has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return any(r[1] == column for r in con.execute(f"PRAGMA table_info({qident(table)})"))
    except sqlite3.OperationalError:
        return False


def rename_tables(con: sqlite3.Connection, *, dry_run: bool) -> list[tuple[str, str, str]]:
    """
    Returns list of (old, new, info). If info contains SQL, it was executed unless dry_run.
    """
    ops: list[tuple[str, str, str]] = []
    for old, new in MAPPING.items():
        old_exists = obj_exists(con, old)
        new_exists = obj_exists(con, new)

        if old_exists and new_exists:
            ops.append((old, new, "SKIP (both exist)"))
            continue
        if (not old_exists) and (not new_exists):
            ops.append((old, new, "SKIP (neither exists)"))
            continue

        if old_exists and not new_exists:
            if is_view(con, old):
                # Can't ALTER VIEW; user must drop/replace manually if they created a view under the old name
                ops.append((old, new, "SKIP (old is a VIEW)"))
                continue
            sql = f"ALTER TABLE {qident(old)} RENAME TO {qident(new)};"
            ops.append((old, new, sql))
            if not dry_run:
                con.execute(sql)
        else:
            ops.append((old, new, "OK (already renamed)"))
    return ops


def create_compat_views(con: sqlite3.Connection, *, dry_run: bool) -> list[tuple[str, str, str]]:
    """
    Create VIEWS with the *old* names that SELECT * from new tables.
    Only created if the old object no longer exists and the new table does exist.
    """
    ops: list[tuple[str, str, str]] = []
    for old, new in MAPPING.items():
        if obj_exists(con, old):
            # If it's already a view, leave it
            if is_view(con, old):
                ops.append((old, new, "VIEW exists"))
            else:
                ops.append((old, new, "SKIP (real table present)"))
            continue
        if not obj_exists(con, new):
            ops.append((old, new, "SKIP (new table missing)"))
            continue
        sql = f"CREATE VIEW IF NOT EXISTS {qident(old)} AS SELECT * FROM {qident(new)};"
        ops.append((old, new, sql))
        if not dry_run:
            con.execute(sql)
    return ops


def add_site_id_indexes(con: sqlite3.Connection, *, dry_run: bool) -> list[tuple[str, str, str]]:
    """
    Add site_id indexes to all data_* tables that have a site_id column.
    """
    ops: list[tuple[str, str, str]] = []
    data_tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'data_%'"
        )
    ]
    for t in data_tables:
        if has_column(con, t, "site_id"):
            idx = f"idx_{t}_site_id"
            exists = (
                con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                    (idx,),
                ).fetchone()
                is not None
            )
            if not exists:
                sql = f"CREATE INDEX {qident(idx)} ON {qident(t)}(site_id);"
                ops.append((t, "site_id", sql))
                if not dry_run:
                    con.execute(sql)
            else:
                ops.append((t, "site_id", "INDEX exists"))
        else:
            ops.append((t, "site_id", "SKIP (no site_id column)"))
    return ops


def foreign_key_check(con: sqlite3.Connection):
    try:
        return con.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.OperationalError as e:
        return [("PRAGMA foreign_key_check failed", str(e))]


def main() -> int:
    ap = argparse.ArgumentParser(description="Rename legacy tables to prefixed names")
    ap.add_argument("--db", default="job_hunt.db", help="Path to SQLite DB")
    ap.add_argument("--dry-run", action="store_true", help="Print operations without executing")
    ap.add_argument("--no-views", action="store_true", help="Do not create compatibility views")
    ap.add_argument("--no-indexes", action="store_true", help="Do not create site_id indexes on data_* tables")
    args = ap.parse_args()

    db = args.db
    if not os.path.exists(db):
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 2

    # 1) Execute rename + optional views/indexes inside a transaction with FK OFF (SQLite limitation)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        ver = sqlite_version(con)
        print(f"SQLite version: {ver}")

        bak_path = None
        if not args.dry_run:
            bak_path = backup_db(db)
            print(f"Backup created: {bak_path}")

        con.execute("PRAGMA foreign_keys = OFF;")
        try:
            con.execute("BEGIN;")

            ren_ops = rename_tables(con, dry_run=args.dry_run)
            view_ops = []
            idx_ops = []

            if not args.no_views:
                view_ops = create_compat_views(con, dry_run=args.dry_run)
            if not args.no_indexes:
                idx_ops = add_site_id_indexes(con, dry_run=args.dry_run)

            if args.dry_run:
                con.execute("ROLLBACK;")
            else:
                con.execute("COMMIT;")
        except Exception as e:
            con.execute("ROLLBACK;")
            print(f"\nERROR during migration: {e}", file=sys.stderr)
            if bak_path:
                print(f"Your backup is at: {bak_path}")
            return 1
        finally:
            con.execute("PRAGMA foreign_keys = ON;")

    # 2) FK check with FK ON
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON;")
        fk = foreign_key_check(con)

    # 3) Report
    print("\n== Rename ops ==")
    for old, new, info in ren_ops:
        print(f"{old:24s} -> {new:26s} : {info}")

    if view_ops:
        print("\n== Compat views ==")
        for old, new, info in view_ops:
            print(f"{old:24s} => view of {new:26s} : {info}")

    if idx_ops:
        print("\n== Index ops ==")
        for t, col, info in idx_ops:
            print(f"{t:26s} .{col} : {info}")

    if fk:
        print("\n!! Foreign key violations detected !!")
        for row in fk:
            try:
                print(dict(row))
            except Exception:
                print(row)
        return 1
    else:
        print("\nForeign keys: OK")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
