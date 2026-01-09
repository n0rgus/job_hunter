"""
Centralised table-name constants + identifier quoting.

Usage:
    from dashboard.db_names import T, qident

    sql = f"SELECT * FROM {qident(T.data_job_listings)} WHERE site_id=?"
    cur.execute(sql, (site_id,))
"""
from types import SimpleNamespace

T = SimpleNamespace(
    system_users="system_users",
    config_sites="config_sites",
    data_keywords="data_keywords",
    data_applications="data_applications",
    data_roles="data_roles",
    config_criteria="config_criteria",
    config_criteria_lists="config_criteria_lists",
    data_job_listings="data_job_listings",
    data_search_run_summary="data_search_run_summary",
)

def qident(name: str) -> str:
    """Quote SQLite identifiers safely (tables/columns)."""
    return '"' + str(name).replace('"', '""') + '"'
