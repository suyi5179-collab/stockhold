"""DuckDB stands in for Athena: the same SQL files, over the same JSON Lines, read from a local mirror."""
from __future__ import annotations

import glob
import os
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"
QUERIES = ["rates_by_product", "wait_before_confirm", "hold_timeout_recommendation", "release_window"]


def connect(events_dir: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    pattern = os.path.join(events_dir, "events", "**", "*.jsonl")
    if not glob.glob(pattern, recursive=True):
        raise FileNotFoundError(f"no event files under {events_dir}")
    # @learn:duckdb-view
    con.execute(f"""CREATE VIEW events AS
        SELECT * FROM read_json_auto('{pattern}', format='newline_delimited', hive_partitioning=true,
                                     union_by_name=true)""")
    # @learn:end duckdb-view
    return con


def run(events_dir: str, name: str, where_sku: str | None = None) -> dict:
    con = connect(events_dir)
    sql = (SQL / f"{name}.sql").read_text()
    if where_sku:
        sql = f"SELECT * FROM ({sql}) WHERE sku = ?"
        cur = con.execute(sql, [where_sku])
    else:
        cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return {"query": name, "sql": (SQL / f"{name}.sql").read_text(), "columns": cols,
            "rows": [dict(zip(cols, [float(v) if hasattr(v, "is_integer") else v for v in r])) for r in cur.fetchall()]}
