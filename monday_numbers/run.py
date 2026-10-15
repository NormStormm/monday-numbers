"""Monday Numbers: clean the week's exports, check them, compute the KPIs, write the Tableau files and the brief.

    .venv/bin/python -m monday_numbers.run              # data/raw -> output/
    .venv/bin/python -m monday_numbers.run --no-brief   # skip the Claude brief (no API call)

If the data-quality gate finds a BLOCK problem (e.g. a missing day of sales), nothing is published:
the report says what's wrong, and the run exits with code 2.
"""
from __future__ import annotations

import argparse
import csv
import os
import pathlib
import sys

import duckdb
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"
EXPORTS = {  # Tableau file -> query
    "daily.csv": "SELECT * FROM daily ORDER BY business_date",
    "weekly.csv": "SELECT * FROM weekly ORDER BY week_start",
    "items_weekly.csv": "SELECT * FROM items_weekly ORDER BY week_start, net_sales DESC",
    "labor_by_role.csv": "SELECT * FROM labor_by_role",
    "vendor_weekly.csv": "SELECT * FROM vendor_weekly",
}


def build(raw: pathlib.Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for f in ("01_clean.sql", "02_kpis.sql", "03_quality.sql"):
        con.execute((SQL / f).read_text().replace("{RAW}", str(raw)))
    return con


def quality_report(con) -> tuple[str, int]:
    rows = con.execute("SELECT severity, check_name, detail FROM quality "
                       "ORDER BY CASE severity WHEN 'BLOCK' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END, check_name").fetchall()
    blocks = sum(1 for r in rows if r[0] == "BLOCK")
    lines = ["# Data-quality report", "",
             "**Status: BLOCKED. Nothing published until the BLOCK items are fixed.**" if blocks else
             "**Status: passed.** Numbers published.", "",
             "| Severity | Check | Detail |", "|---|---|---|"]
    lines += [f"| {s} | {c} | {d} |" for s, c, d in rows]
    return "\n".join(lines) + "\n", blocks


def export(con, out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, query in EXPORTS.items():
        cur = con.execute(query)
        with open(out / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(cur.fetchall())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--no-brief", action="store_true")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    con = build(pathlib.Path(args.raw))
    report, blocks = quality_report(con)
    (out / "quality_report.md").write_text(report)
    print(report)
    if blocks:
        print(f"{blocks} BLOCK problem(s): no KPIs or brief published. Fix the data and run again.")
        return 2

    export(con, out / "tableau")
    print(f"Wrote {len(EXPORTS)} Tableau files to {out / 'tableau'}")
    con.sql("SELECT week_start, round(net_sales) AS sales, covers, labor_pct, food_cost_pct, prime_cost_pct "
            "FROM weekly ORDER BY week_start DESC LIMIT 3").show()

    if not args.no_brief:
        load_dotenv(ROOT / ".env")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("No ANTHROPIC_API_KEY in .env: skipping the weekly brief.")
        else:
            from .brief import write_brief
            result = write_brief(con, out)
            print(result["text"])
            print(f"\nNumber check: {result['checked']} numbers verified"
                  + (f", {len(result['unverified'])} NOT found in the data: {result['unverified']}" if result["unverified"] else ", all match the data")
                  + f"  (${result['cost']:.4f})")
            if result["unverified"]:
                return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
