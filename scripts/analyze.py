#!/usr/bin/env python3
"""Athena stand-in: mirror s3://stockhold-events/events/ locally, run sql/*.sql with DuckDB.

    .venv/bin/python scripts/analyze.py [--no-download] [--dir .runtime/eventlog] [--out docs/results/analysis.md]
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:45671")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from stockhold import analytics, eventlog  # noqa: E402


def table(res: dict) -> str:
    cols = res["columns"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in res["rows"]:
        out.append("| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".runtime/eventlog")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--sku-like", default="", help="only rows whose sku contains this")
    ap.add_argument("--out")
    a = ap.parse_args()
    if not a.no_download:
        print(f"downloaded {eventlog.download(a.dir)} new event files to {a.dir}", file=sys.stderr)
    parts = []
    for q in analytics.QUERIES:
        res = analytics.run(a.dir, q)
        if a.sku_like and "sku" in res["columns"]:
            res["rows"] = [r for r in res["rows"] if a.sku_like in str(r["sku"])]
        parts.append(f"### {q}\n\n{table(res)}\n")
    text = "\n".join(parts)
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()
