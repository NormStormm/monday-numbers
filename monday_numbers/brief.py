"""The weekly brief: Claude explains the week in plain English, and code checks every number it quotes.

Claude never sees the raw data or does the math. It gets a small "facts" sheet computed in SQL and is
told to use only those numbers. Then every number in its brief is matched back to the facts; anything
that can't be matched is flagged, the brief is retried once with the problem pointed out, and if it's
still wrong it's published with a warning instead of quietly.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import date, datetime
from decimal import Decimal

import anthropic

MODEL = "claude-opus-5-5"
PRICE = (4, 20)   # $ per 1M input / output tokens

SYSTEM = """You write the Monday-morning brief for a hotel restaurant's general manager. You receive a facts sheet for the week that just ended, already calculated. Write what changed, why, and what to look at this week.

Format (plain text, no markdown headers):
- One headline sentence with the week's sales and how they compare with the prior week.
- 3 to 5 bullets, each starting with "- ", on what moved and the most likely reason visible in the facts (a specific day, role, item, vendor or price change). Lead with what matters most for profit.
- A final line starting "This week: " with one or two specific things to check or do.

Rules:
1. Use only numbers that appear in the facts sheet. Never calculate new numbers (no new differences, sums or averages). If a comparison you want isn't in the facts, describe it in words without a number.
2. Percentages: write 0.3554 as 35.5%. Point changes: write 0.0093 as 0.9 points. Money: $59,796 or $59.8K.
3. Labor % = labor cost / sales. Food cost % = food cost of goods / food sales. Prime cost = labor + all cost of goods, as % of sales. Lower is better for all three.
4. Say "likely" or "points to" for causes; the facts show what happened, not why for certain.
5. Mention data-quality warnings only if they change how much to trust a number.
6. Under 170 words."""


def facts(con) -> dict:
    q = lambda sql: [dict(zip([c[0] for c in cur.description], r)) for cur in [con.execute(sql)] for r in cur.fetchall()]
    weeks = q("SELECT * FROM weekly ORDER BY week_start DESC LIMIT 5")
    last, prior = weeks[0], weeks[1]
    lw = last["week_start"]
    week_end = q(f"SELECT max(business_date) d FROM daily WHERE week_start = '{lw}'")[0]["d"]
    keep = ["net_sales", "covers", "avg_check_per_guest", "labor_hours", "labor_cost", "labor_pct", "sales_per_labor_hour",
            "overtime_hours", "food_cost_pct", "bar_cost_pct", "prime_cost_pct"]
    f = {
        "week": f"{lw:%a %b %d} to {week_end:%a %b %d, %Y}",
        "this_week": {k: last[k] for k in keep},
        "prior_week": {k: prior[k] for k in keep},
        "changes_vs_prior_week": {
            "sales_change_pct": last["sales_change_vs_prior_week"], "labor_pct_change_points": last["labor_pct_change_pts"],
            "food_cost_pct_change_points": last["food_cost_pct_change_pts"], "avg_check_change": last["avg_check_change"]},
        "four_week_average_before_this_week": q(f"""
            SELECT round(avg(net_sales), 2) net_sales, round(avg(labor_pct), 4) labor_pct, round(avg(food_cost_pct), 4) food_cost_pct
            FROM weekly WHERE week_start < '{lw}' AND week_start >= '{lw}'::DATE - INTERVAL 28 DAY""")[0],
        "days_this_week_vs_same_day_last_week": q(f"""
            SELECT t.weekday, round(t.net_sales, 2) sales, t.covers, t.labor_hours, t.labor_pct,
                   round(p.net_sales, 2) sales_last_week, p.covers covers_last_week, p.labor_hours labor_hours_last_week,
                   p.labor_pct labor_pct_last_week
            FROM daily t JOIN daily p ON p.business_date = t.business_date - INTERVAL 7 DAY
            WHERE t.week_start = '{lw}' ORDER BY t.business_date"""),
        "servers_by_day_this_week": q(f"""
            SELECT weekday, daypart, shifts, hours FROM labor_by_role
            WHERE week_start = '{lw}' AND role = 'Server' ORDER BY business_date, daypart"""),
        "top_items_this_week": q(f"SELECT item, qty_sold, round(net_sales, 2) net_sales FROM items_weekly WHERE week_start = '{lw}' ORDER BY net_sales DESC LIMIT 3"),
        "biggest_item_unit_changes_vs_prior_4_week_avg": q(f"""
            WITH t AS (SELECT item, qty_sold FROM items_weekly WHERE week_start = '{lw}'),
                 p AS (SELECT item, avg(qty_sold) q, max(avg_price) mx, min(avg_price) mn FROM items_weekly
                       WHERE week_start < '{lw}' AND week_start >= '{lw}'::DATE - INTERVAL 28 DAY GROUP BY item)
            SELECT t.item, t.qty_sold, round(p.q, 1) prior_4wk_avg_qty, round(t.qty_sold / p.q - 1, 4) change_pct
            FROM t JOIN p USING (item) ORDER BY abs(t.qty_sold / p.q - 1) DESC LIMIT 3"""),
        "price_changes_in_last_8_weeks": q(f"""
            WITH x AS (SELECT item, week_start, avg_price, lag(avg_price) OVER (PARTITION BY item ORDER BY week_start) prev
                       FROM items_weekly WHERE week_start >= '{lw}'::DATE - INTERVAL 56 DAY)
            SELECT item, week_start, prev old_price, avg_price new_price FROM x WHERE abs(avg_price - prev) >= 1"""),
        "vendor_purchases_vs_prior_4_week_avg": q(f"""
            WITH t AS (SELECT vendor, purchases FROM vendor_weekly WHERE week_start = '{lw}'),
                 p AS (SELECT vendor, avg(purchases) a FROM vendor_weekly
                       WHERE week_start < '{lw}' AND week_start >= '{lw}'::DATE - INTERVAL 28 DAY GROUP BY vendor)
            SELECT vendor, round(t.purchases, 2) this_week, round(p.a, 2) prior_4wk_avg, round(t.purchases / p.a - 1, 4) change_pct
            FROM t JOIN p USING (vendor) ORDER BY abs(t.purchases / p.a - 1) DESC LIMIT 3"""),
        "data_quality_warnings": [r["detail"] for r in q("SELECT detail FROM quality WHERE severity = 'WARN'")],
    }
    def clean(v):                                       # plain JSON: decimals -> rounded numbers, dates -> text
        if isinstance(v, dict): return {k: clean(x) for k, x in v.items()}
        if isinstance(v, list): return [clean(x) for x in v]
        if isinstance(v, (float, Decimal)): return round(float(v), 4)
        if isinstance(v, (date, datetime)): return v.isoformat()
        return v
    return clean(f)


# ── Number check ──
NUM = re.compile(r"(?<![\w.])(-?\$?-?[\d,]*\.?\d+)\s*(%|K\b|k\b| points?\b| pts?\b)?")
DATE_CONTEXT = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(,\s*\d{4})?")


def allowed_values(obj, out=None) -> list[float]:
    out = [] if out is None else out
    if isinstance(obj, dict):
        for v in obj.values(): allowed_values(v, out)
    elif isinstance(obj, list):
        for v in obj: allowed_values(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out += [float(obj), abs(float(obj))]
        if abs(obj) < 1:                                    # ratios: 0.3554 -> 35.54 (%), 0.0093 -> 0.93 (points)
            out += [abs(float(obj)) * 100]
    elif isinstance(obj, str):
        for m in re.finditer(r"-?\d+\.?\d*", obj):          # numbers inside text facts (e.g. warnings)
            out.append(float(m.group()))
    return out


def check_numbers(text: str, facts_: dict) -> tuple[int, list[str]]:
    allowed = allowed_values(facts_)
    scrubbed = DATE_CONTEXT.sub(" ", text)                  # "Oct 6" and "Oct 6, 2026" are dates, not figures
    checked, bad = 0, []
    for m in NUM.finditer(scrubbed):
        raw, unit = m.group(1), (m.group(2) or "").strip().lower()
        n = float(raw.replace("$", "").replace(",", ""))
        if unit in ("k",):
            n *= 1000
        if "$" not in raw and not unit and float(n).is_integer() and abs(n) <= 7:
            continue                                        # small counts ("two servers", "3 bullets") aren't figures
        checked += 1
        tol = 0.051 if unit in ("%", "point", "points", "pt", "pts") or abs(n) < 100 else max(0.006 * abs(n), 0.51)
        if unit == "k":
            tol = 50.01                                     # "$59.8K" is rounded to the nearest $100
        if not any(abs(abs(n) - a) <= tol for a in allowed):
            bad.append(m.group(0).strip())
    return checked, bad


def write_brief(con, out: pathlib.Path, model: str = MODEL) -> dict:
    f = facts(con)
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": "Facts sheet for the week (JSON):\n<facts>\n" + json.dumps(f, indent=1) + "\n</facts>\nWrite the brief."}]
    tokens_in = tokens_out = 0
    for attempt in (1, 2):
        r = client.messages.create(model=model, max_tokens=2000, system=SYSTEM, messages=messages)
        tokens_in += r.usage.input_tokens; tokens_out += r.usage.output_tokens
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        checked, bad = check_numbers(text, f)
        if not bad:
            break
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"These numbers aren't in the facts sheet: {', '.join(bad)}. Rewrite the brief using only numbers from the facts (describe anything else in words)."}]
    header = f"Lanai Grill weekly brief: {f['week']}\n\n"
    if bad:
        header += f"⚠ NUMBER CHECK FAILED: {', '.join(bad)} could not be matched to the data. Verify before sharing.\n\n"
    footer = f"\n\n(Written by Claude; every number checked against the week's data: {checked} verified.)" if not bad else ""
    (out / "brief.txt").write_text(header + text + footer + "\n")
    (out / "brief_facts.json").write_text(json.dumps(f, indent=1))
    return {"text": header + text + footer, "checked": checked, "unverified": bad, "attempts": attempt,
            "cost": (tokens_in * PRICE[0] + tokens_out * PRICE[1]) / 1e6}
