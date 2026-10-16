"""Tests for the cleaning, the data-quality gate, the KPIs and the brief's number check.
No API calls, no cost:  .venv/bin/python -m pytest -q
"""
import json
import pathlib
import shutil

import pytest

from monday_numbers.brief import check_numbers
from monday_numbers.run import build, quality_report

ROOT = pathlib.Path(__file__).resolve().parent.parent
FACTS = json.loads((ROOT / "tests" / "known_facts.json").read_text())


@pytest.fixture(scope="module")
def blocked():
    """The raw exports as delivered: Saturday Oct 10 missing."""
    return build(ROOT / "data" / "raw")


@pytest.fixture(scope="module")
def ready(tmp_path_factory):
    """The same exports after the late file arrives."""
    d = tmp_path_factory.mktemp("raw")
    for f in list((ROOT / "data" / "raw").glob("*.csv")) + list((ROOT / "data" / "late").glob("*.csv")):
        shutil.copy(f, d)
    return build(d)


def rows(con, sql):
    return con.execute(sql).fetchall()


def test_gate_blocks_on_the_missing_day(blocked):
    report, blocks = quality_report(blocked)
    assert blocks == 1
    assert "Missing POS day" in report and "Sat Oct 10" in report


def test_gate_passes_once_the_late_file_arrives(ready):
    report, blocks = quality_report(ready)
    assert blocks == 0 and "passed" in report


def test_reexported_rows_removed(ready):
    [(day, n)] = rows(ready, "SELECT business_date::VARCHAR, count(*) FROM pos_duplicates GROUP BY 1")
    assert day == FACTS["duplicated_day"] and n > 0
    assert rows(ready, "SELECT count(*) FROM pos GROUP BY business_date, item, is_void HAVING count(*) > 1") == []


def test_product_mix_reconciles_to_pos_total(ready):
    [(total,)] = rows(ready, "SELECT sum(net_sales) FROM daily")
    assert float(total) == pytest.approx(FACTS["daily_summary_net_sales_total"], abs=0.01)


def test_both_date_formats_parsed(ready):
    assert rows(ready, "SELECT count(*) FROM pos_parsed WHERE business_date IS NULL") == [(0,)]
    assert rows(ready, "SELECT count(DISTINCT old_date_format) FROM pos_parsed") == [(2,)]


def test_overnight_shifts_end_after_they_start(ready):
    assert rows(ready, "SELECT count(*) FROM shifts WHERE clock_out <= clock_in") == [(0,)]
    assert rows(ready, "SELECT max(hours) <= 13 FROM shifts") == [(True,)]


def test_forgotten_clock_out_is_estimated_not_dropped(ready):
    [(day, hours)] = rows(ready, "SELECT business_date::VARCHAR, hours FROM shifts WHERE clock_out_missing")
    assert day == FACTS["forgot_clock_out_day"] and 6 <= hours <= 10


def test_names_standardized(ready):
    assert rows(ready, "SELECT count(DISTINCT employee) = count(DISTINCT emp_id) FROM shifts") == [(True,)]


def test_overtime_only_past_40_hours(ready):
    bad = rows(ready, """SELECT emp_id, week_start FROM shifts_paid GROUP BY ALL
                         HAVING abs(sum(overtime_hours) - greatest(0, sum(hours) - 40)) > 0.01""")
    assert bad == []


def test_planted_stories_show_up(ready):
    last = FACTS["latest_week_start"]
    [(tue,)] = rows(ready, f"SELECT labor_pct FROM daily WHERE business_date = '{FACTS['overstaffed_day']}'")
    [(other,)] = rows(ready, f"SELECT max(labor_pct) FROM daily WHERE week_start = '{last}' AND weekday NOT IN ('Tuesday', 'Sunday')")
    assert tue > other + 0.10                                       # the overstaffed Tuesday stands out
    spike = rows(ready, f"SELECT food_cost_pct FROM weekly WHERE week_start >= '{FACTS['produce_spike_from_week_start']}'")
    before = rows(ready, f"SELECT max(food_cost_pct) FROM weekly WHERE week_start < '{FACTS['produce_spike_from_week_start']}'")[0][0]
    assert all(v > before for (v,) in spike)                        # food cost steps up with produce prices


FACTS_SHEET = {"this_week": {"net_sales": 59795.5, "labor_pct": 0.3554}, "prior_week": {"net_sales": 58948.5},
               "changes_vs_prior_week": {"sales_change_pct": 0.0144, "labor_pct_change_points": 0.0093}}


@pytest.mark.parametrize("text, bad", [
    ("Sales were $59,796, up 1.4%.", []),
    ("Sales were $59.8K vs $58.9K.", []),
    ("Labor 35.5%, up 0.9 points.", []),
    ("Labor rose to 37.2%.", ["37.2%"]),                 # not in the facts
    ("Sales hit $61,200.", ["$61,200"]),
    ("Tuesday Oct 6, 2026 had two extra servers.", []),  # dates and small counts aren't checked
])
def test_number_check(text, bad):
    assert check_numbers(text, FACTS_SHEET)[1] == bad
