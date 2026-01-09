# db_utils.py
import sqlite3
from typing import Iterable, Optional, Dict, Any, Tuple
from db_names import T, qident

DB_FILE = "job_hunt.db"  # ensure this points to the same DB as db_helpers

log = logging.getLogger("job_hunter.db")

def get_table_columns(con: sqlite3.Connection, table: str) -> Dict[str, Dict[str, Any]]:
    """Return {col_name: {pk:0/1, notnull:0/1, type:"TEXT"...}} for a table or view."""
    rows = con.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    return {r[1]: {"pk": r[5], "notnull": r[3], "type": r[2]} for r in rows}

def detect_conflict_target(con: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    """
    Decide the ON CONFLICT target. Prefer a single PK 'listing_id';
    otherwise try ('site_id','listing_id'); else fall back to the PK set.
    """
    cols = get_table_columns(con, table)
    # Common patterns
    if "listing_id" in cols and cols["listing_id"]["pk"]:
        return ("listing_id",)
    if "site_id" in cols and "listing_id" in cols:
        # requires UNIQUE(site_id, listing_id) index in schema
        return ("site_id", "listing_id")
    # generic: all PK columns in order
    pk_cols = [name for name, meta in cols.items() if meta["pk"]]
    if pk_cols:
        return tuple(pk_cols)
    # fallback (not ideal, but prevents crash)
    return ("listing_id",)

def format_sql_with_params(sql: str, params: Iterable[Any]) -> str:
    """Best-effort pretty string for SQL with '?' placeholders (for logging)."""
    def q(v):
        if v is None: return "NULL"
        if isinstance(v, (int, float)): return str(v)
        s = str(v).replace("'", "''")
        return f"'{s}'"
    parts = sql.split("?")
    out = []
    it = iter(params)
    for i, p in enumerate(parts):
        out.append(p)
        if i < len(parts) - 1:
            try:
                out.append(q(next(it)))
            except StopIteration:
                out.append("?")
    return "".join(out)

def direct_upsert_listing_row(
    listing_id: str,
    keyword_id: int,
    title: str,
    company: str,
    location: str,
    url: str,
    site_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
    """
    SQLite UPSERT into data_job_listings.
    Returns (success, 'inserted'|'updated'|'ignored ...'|'IntegrityError:...'|'Exception:...')
    """
    table = T.data_job_listings  # NEW name
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        cols = _get_table_columns(conn, table)
        colset = {c.lower() for c in cols}
        required = {"listing_id", "keyword_id"}
        if not required.issubset(colset):
            return False, f"{table} missing required columns: have {cols}"

        # Build payload using only existing cols
        payload: Dict[str, Any] = {
            "listing_id": listing_id,
            "keyword_id": keyword_id,
            "title": title,
            "company": company,
            "location": location,
            "url": url,
        }
        if "site_id" in colset:
            payload["site_id"] = site_id if site_id is not None else 1
        if "status" in colset:
            payload["status"] = "new"
        # captured_at handled by CURRENT_TIMESTAMP if present
        has_captured_at = "captured_at" in colset

        columns = list(payload.keys())
        placeholders = ",".join("?" for _ in columns)
        values = [payload[c] for c in columns]

        # Pre-check: does this row already exist in DB?
        where = "listing_id = ?"
        where_params: List[Any] = [listing_id]
        if "site_id" in colset and site_id is not None:
            where += " AND site_id = ?"
            where_params.append(site_id)
        exists = bool(conn.execute(
            f"SELECT 1 FROM {qident(table)} WHERE {where} LIMIT 1", where_params
        ).fetchone())

        # ON CONFLICT target
        conflict_cols = _detect_conflict_target(conn, table)

        # SET clause for DO UPDATE (exclude conflict columns)
        set_cols = [c for c in columns if c not in conflict_cols]
        set_clause = ", ".join([f"{qident(c)}=excluded.{qident(c)}" for c in set_cols]) or "/* no-op */"

        # Optional captured_at column appended at the end of INSERT
        col_sql = ", ".join(qident(c) for c in columns) + (", captured_at" if has_captured_at else "")
        values_sql_suffix = ", CURRENT_TIMESTAMP" if has_captured_at else ""

        sql = (
            f"INSERT INTO {qident(table)} ({col_sql}) VALUES ({placeholders}{values_sql_suffix}) "
            f"ON CONFLICT ({', '.join(qident(c) for c in conflict_cols)}) DO UPDATE SET {set_clause}"
        )

        try:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
        except sqlite3.Error as e:
            rendered = _format_sql_with_params(sql, values)
            log.error("[UPSERT-FAILED] %s", e)
            log.error("[UPSERT-SQL] %s", rendered)
            return False, f"Exception: {e}"

        return True, ("updated" if exists else "inserted")

    except sqlite3.IntegrityError as e:
        return False, f"IntegrityError: {e}"
    except Exception as e:
        return False, f"Exception: {e}"
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass

def upsert_job_listing(con: sqlite3.Connection, row: Dict[str, Any]) -> str:
    """
    Generic SQLite UPSERT into data_job_listings.
    Returns: 'inserted' | 'updated'
    """
    table = T.data_job_listings
    cols_meta = get_table_columns(con, table)
    if not cols_meta:
        raise RuntimeError(f"Table {table} not found or has no columns")

    # keep only known columns; avoid introducing dots/aliases in names
    allowed = {k: v for k, v in row.items() if k in cols_meta}
    if not allowed:
        raise RuntimeError(f"No matching columns in payload for {table}: keys={list(row)}")

    columns = list(allowed.keys())
    placeholders = ",".join(["?"] * len(columns))
    values = [allowed[c] for c in columns]

    conflict_cols = detect_conflict_target(con, table)

    # build SET clause excluding conflict columns
    set_cols = [c for c in columns if c not in conflict_cols]
    set_clause = ", ".join([f"{qident(c)}=excluded.{qident(c)}" for c in set_cols]) or "/* no-op */"

    sql = (
        f"INSERT INTO {qident(table)} ({', '.join(qident(c) for c in columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(qident(c) for c in conflict_cols)}) DO UPDATE SET {set_clause}"
    )

    try:
        cur = con.cursor()
        cur.execute(sql, values)
        # rowcount == 1 on insert, == 1 on update too (SQLite); infer via changes() if needed
        return "updated" if cur.lastrowid is None else "inserted"
    except sqlite3.Error as e:
        # LOG the exact SQL and params so we can see what SQLite saw
        rendered = format_sql_with_params(sql, values)
        log.debug("[UPSERT-FAILED] %s", e)
        log.debug("[UPSERT-SQL] %s", rendered)
        # rethrow so caller can count failures
        raise

def get_site_config(site_id: int) -> Dict[str, Any]:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            f"""
            SELECT site_id, site_name, url, url_prefix, url_suffix,
                   tag_for_result_count, tag_for_cards, tag_for_title,
                   tag_for_company, tag_for_location, tag_for_posted_on
            FROM {qident(T.config_sites)}
            WHERE site_id = ?
            """,
            (site_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Site ID {site_id} not found in {T.config_sites}")
        return dict(row)
    finally:
        con.close()

def list_site_configs() -> Iterable[Dict[str, Any]]:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"""
            SELECT site_id, site_name, url, url_prefix, url_suffix,
                   tag_for_result_count, tag_for_cards, tag_for_title,
                   tag_for_company, tag_for_location, tag_for_posted_on
            FROM {qident(T.config_sites)}
            ORDER BY site_id
            """
        ).fetchall()
        for r in rows:
            yield dict(r)
    finally:
        con.close()

def get_active_keywords():
    """ss
    Return a list of (keyword_id, keyword) for active/enabled keywords.
    Schema-flexible:
      - If Keywords.active doesn't exist, it won't filter on it.
      - If Roles.enabled exists and Keywords.role_id is present, it joins Roles and filters enabled roles.
      - Detects common column names for id (keyword_id/id) and name (keyword/name).
    """
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()

        def _cols(table_name: str):
            try:
                c.execute(f"PRAGMA table_info({qident(table_name)})")
                return {row[1] for row in c.fetchall()}  # column name at index 1
            except Exception:
                return set()

        # Prefer new names; fall back to legacy views if present
        kw_table = T.data_keywords
        roles_table = T.data_roles

        kw_cols = _cols(kw_table) or _cols("Keywords")
        roles_cols = _cols(roles_table) or _cols("Roles")

        # Choose ID and name columns
        id_col = 'keyword_id' if 'keyword_id' in kw_cols else ('id' if 'id' in kw_cols else None)
        name_col = 'keyword'    if 'keyword'    in kw_cols else ('name' if 'name' in kw_cols else None)
        if not id_col or not name_col:
            return []

        # Determine if we can/should join Roles
        join_roles = ('role_id' in kw_cols) and ('role_id' in roles_cols)

        where = []
        # Keyword active flags (optional)
        for cand in ('active', 'enabled', 'is_active'):
            if cand in kw_cols:
                where.append(f"k.{cand}=1")
                break

        # Roles enabled (optional)
        if join_roles and 'enabled' in roles_cols:
            where.append("r.enabled=1")

        if join_roles:
            sql = (
                f"SELECT k.{id_col}, k.{name_col} "
                f"FROM {qident(kw_table)} k JOIN {qident(roles_table)} r ON k.role_id = r.role_id"
            )
        else:
            sql = f"SELECT k.{id_col}, k.{name_col} FROM {qident(kw_table)} k"

        if where:
            sql += " WHERE " + " AND ".join(where)

        c.execute(sql)
        rows = c.fetchall()
        return rows
    finally:
        conn.close()

def insert_job_listing(**job):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute(
            f"INSERT INTO {qident(T.data_job_listings)}"
            "(listing_id, keyword_id, title, company, location, url, listing_date, suitability_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["listing_id"], job["keyword_id"], job["title"], job["company"],
                job["location"], job["url"], job.get("listing_date"), job.get("suitability_score", 0)
            )
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # duplicate primary key etc.
        pass
    finally:
        conn.close()

def insert_run_summary(keyword_id, listings_found, highly_suitable, applications_made=0, results_returned=0, skipped_duplicates=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        f"INSERT INTO {qident(T.data_search_run_summary)}"
        "(keyword_id, listings_found, highly_suitable, applications_made) "
        "VALUES (?,?,?,?)",
        (keyword_id, listings_found, highly_suitable, applications_made)
    )
    conn.commit()
    conn.close()
