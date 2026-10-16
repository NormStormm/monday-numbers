# Monday Numbers runbook

## Set up (once)
Python **3.10+** (the current Claude SDK won't install on 3.9).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

For the brief only, save a [Claude API key](https://console.anthropic.com/) in `.env` (never committed):

```bash
read -s "k?Paste your Claude API key: " && printf 'ANTHROPIC_API_KEY=%s\n' "$k" > .env && chmod 600 .env && unset k
```

## Each Monday
1. Drop the week's exports into `data/raw/`: POS product mix (`pos_productmix_*.csv`), `pos_daily_summary.csv`, `timeclock_export.csv`, `purchases.csv`, `inventory_counts.csv`.
2. Run:
   ```bash
   .venv/bin/python -m monday_numbers.run
   ```
3. Read `output/quality_report.md`:
   - **BLOCKED** (exit code 2): nothing was published. Fix each BLOCK row (usually re-export a missing day) and run again.
   - **Passed:** `output/tableau/*.csv` and `output/brief.txt` are written.
4. Read the brief. If the number check failed (exit code 3), the brief starts with a ⚠ line naming the numbers that couldn't be matched. Don't share it until they're checked.
5. Refresh the dashboard: open the workbook in Tableau → **Data → Refresh All Extracts** (or re-point the data sources at `output/tableau/`), then **File → Save to Tableau Public**.

Options: `--raw <folder>` (read exports from elsewhere), `--out <folder>`, `--no-brief` (skip the Claude call).

## What the gate checks

| Severity | Check | Fix |
|---|---|---|
| BLOCK | **Missing POS day**: the POS summary has sales but the product mix has no rows | Re-export that day from the POS |
| BLOCK | **Sales don't reconcile**: product mix and POS summary more than 0.5% apart | Usually a partial export; re-export the day |
| BLOCK | **Conflicting duplicate rows**: the same item and day with different amounts | Find out which export is right; remove the other |
| BLOCK | **Missing inventory count**: food cost can't be calculated | Enter the count |
| BLOCK | **Negative sales day** | Check voids and refunds in the POS |
| WARN | **Missing clock-out**: hours estimated from that role's typical shift | Manager confirms or corrects the punch |
| WARN | **Very long shift** (over 13 hours) | Likely a punch mistake; correct it |
| FIXED | Duplicates removed, overnight shifts corrected, formats and names standardized | Nothing to do; listed for transparency |

## KPI definitions
- **Labor %** = wages (including 1.5× overtime past 40 hours a week) ÷ net sales. Wages only; no taxes or benefits.
- **Sales per labor hour** = net sales ÷ hours worked.
- **Food cost %** = food cost of goods ÷ food sales, where cost of goods = opening inventory + purchases − closing inventory (Produce, Seafood, Meat, Dairy, Dry goods).
- **Bar cost %** = same for Beverage + Alcohol.
- **Prime cost %** = (labor + all cost of goods) ÷ net sales.
- **Average check** = net sales ÷ guests (covers). Weeks run Monday to Sunday.

## Tableau notes
- Connect with **Text file**, not Microsoft Excel (the Excel connector rejects .csv).
- Set percentage fields' **Default Properties → Number Format → Percentage**.
- On trend lines, untick **Include zero** on the axis, or small changes disappear.
- The menu total uses a FIXED calculation: `{ SUM([Net Sales]) }`.
- Tableau Public saves to the web (public). Use only data you're allowed to share.

## Cost
The brief is one Claude call of about 4,000 input tokens: roughly **4¢** with Claude Opus 5.5. Everything else is free and runs locally.
