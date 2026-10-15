"""Creates 12 weeks of fake exports for "Lanai Grill" (a fictitious hotel restaurant), messy the way real exports are.

    .venv/bin/python tools/make_sample_data.py

Writes data/raw/ (what the systems export) and data/late/ (a file that "arrives late"), plus
tests/known_facts.json: the stories planted in the data, so we can check the brief finds them.

Planted mess (the cleaning step must handle these):
  - The POS switched date format mid-period (MM/DD/YYYY -> YYYY-MM-DD) and dropped "$1,234.50" text amounts.
  - One weekly POS file has Wednesday's rows twice (it was re-exported and appended).
  - Saturday Oct 10 is missing from the POS export (it shows up later in data/late/).
  - Voided items appear as negative rows.
  - The time clock writes the clock-out date wrong for shifts that cross midnight, has inconsistent
    name capitalization, a duplicated punch, and one cook who forgot to clock out.
Planted stories (the brief should find these in the latest week):
  - Tuesday Oct 6 had two extra servers scheduled for a group that cancelled -> labor % jumps.
  - Ewa Plains Produce raised prices ~18% from week 11 -> food cost % rises.
  - The Ahi Poke Bowl went from $24 to $26 in week 6, and its sales count dipped.
"""
import csv
import json
import math
import pathlib
import random
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW, LATE, TESTS = ROOT / "data" / "raw", ROOT / "data" / "late", ROOT / "tests"
START = date(2026, 7, 20)                 # a Monday
WEEKS = 12
FORMAT_SWITCH_WEEK = 8                    # POS update: new date format and plain-number amounts from this week on
DUP_WEEK, DUP_DAY = 9, 2                  # week 9, Wednesday duplicated
MISSING_DAY = date(2026, 10, 10)          # Saturday of week 12
OVERSTAFF_DAY = date(2026, 10, 6)         # Tuesday of week 12
PRODUCE_SPIKE_WEEK = 11
POKE_PRICE_WEEK = 6
random.seed(42)

# item, category, price, popularity weight within its group, cost ratio
MENU = [
    ("Loco Moco", "Food", 22.00, 1.6, 0.30), ("Ahi Poke Bowl", "Food", 24.00, 1.4, 0.36), ("Kalua Pork Plate", "Food", 21.00, 1.1, 0.27),
    ("Garlic Shrimp", "Food", 26.00, 1.0, 0.35), ("Fish Tacos", "Food", 19.00, 0.9, 0.31), ("Huli Huli Chicken", "Food", 23.00, 1.0, 0.28),
    ("Lanai Burger", "Food", 20.00, 1.2, 0.30), ("Mahi Sandwich", "Food", 21.00, 0.8, 0.33), ("Kale Caesar", "Food", 16.00, 0.35, 0.24),
    ("Kids Mac & Cheese", "Food", 11.00, 0.4, 0.22),
    ("Spam Musubi", "Food", 9.00, 1.2, 0.25), ("Coconut Shrimp", "Food", 15.00, 1.0, 0.32),        # starters
    ("Malasadas", "Food", 10.00, 1.2, 0.20), ("Haupia Pie", "Food", 9.00, 0.8, 0.22),              # desserts
    ("Kona Coffee", "Beverage", 5.00, 1.0, 0.18), ("Iced Tea", "Beverage", 4.50, 1.0, 0.10), ("POG Juice", "Beverage", 6.00, 0.8, 0.25),
    ("Soda", "Beverage", 4.00, 0.9, 0.12),
    ("Mai Tai", "Alcohol", 16.00, 1.5, 0.22), ("Local Lager", "Alcohol", 9.00, 1.2, 0.28), ("House Wine", "Alcohol", 13.00, 0.8, 0.26),
    ("Lava Flow", "Alcohol", 15.00, 0.7, 0.23),
]
GROUPS = {  # how many of each per guest (cover)
    "main": (["Loco Moco", "Ahi Poke Bowl", "Kalua Pork Plate", "Garlic Shrimp", "Fish Tacos", "Huli Huli Chicken", "Lanai Burger",
              "Mahi Sandwich", "Kale Caesar", "Kids Mac & Cheese"], 0.96),
    "starter": (["Spam Musubi", "Coconut Shrimp"], 0.32),
    "dessert": (["Malasadas", "Haupia Pie"], 0.24),
    "bev": (["Kona Coffee", "Iced Tea", "POG Juice", "Soda"], 0.72),
    "alcohol": (["Mai Tai", "Local Lager", "House Wine", "Lava Flow"], 0.48),
}
ITEM = {m[0]: m for m in MENU}
WEEKDAY_FACTOR = [0.80, 0.74, 0.86, 0.95, 1.25, 1.36, 1.14]

STAFF = [  # id, name as entered in HR, role, hourly rate (Hawaii minimum wage is $16.00 in 2026)
    ("E101", "Leilani Kahale", "Server", 16.00), ("E102", "Marcus Tanaka", "Server", 16.00), ("E103", "Jenna Akana", "Server", 16.00),
    ("E104", "Kai Moreno", "Server", 16.00), ("E105", "Noelani Cruz", "Server", 16.00), ("E106", "Devin Park", "Server", 16.00),
    ("E107", "Aiko Sato", "Server", 16.00), ("E108", "Tomas Reyes", "Server", 16.00),
    ("E111", "Keoni Lee", "Busser", 16.50), ("E112", "Ana Ramos", "Busser", 16.50), ("E113", "Ryan Fong", "Busser", 16.50),
    ("E121", "Mele Kalani", "Host", 17.00), ("E122", "Grace Ito", "Host", 17.00),
    ("E131", "Sione Fifita", "Bartender", 16.50), ("E132", "Kara Wong", "Bartender", 16.50),
    ("E141", "Duke Kamaka", "Line Cook", 24.00), ("E142", "Rosa Delgado", "Line Cook", 24.00), ("E143", "Ben Nakamura", "Line Cook", 24.00),
    ("E144", "Lani Souza", "Line Cook", 24.00), ("E145", "Chris Yamada", "Line Cook", 24.00),
    ("E151", "Paolo Garcia", "Sous Chef", 30.00),
    ("E161", "Joe Kealoha", "Dishwasher", 19.00), ("E162", "Vic Santos", "Dishwasher", 19.00),
]
# role -> list of (shift start hour, length in hours, people at normal volume, extra people per +100 covers)
SHIFTS = {
    "Server":     [(10, 5.5, 2, 1.0), (16, 6.5, 3, 1.4)],
    "Busser":     [(10.5, 5, 1, 0.5), (16.5, 6, 1, 0.7)],
    "Host":       [(10, 5.5, 1, 0), (16, 6, 1, 0.3)],
    "Bartender":  [(17, 8, 1, 0.5)],                                # closes at 1am: crosses midnight
    "Line Cook":  [(9, 7, 2, 0.6), (15.5, 8, 2, 0.9)],
    "Sous Chef":  [(11, 9, 1, 0)],
    "Dishwasher": [(10, 6, 1, 0), (16, 8, 1, 0.3)],
}
VENDORS = [  # vendor (same fictitious vendors as Paper Chaser), category, share of that category's purchases
    ("Ewa Plains Produce Co.", "Produce"), ("Makai Fish Market Wholesale", "Seafood"), ("Mauka Meats (fictitious)", "Meat"),
    ("Island Dairy Supply (fictitious)", "Dairy"), ("Kalo Paper & Janitorial Supply", "Dry goods"), ("Koolau Coffee Roasters", "Beverage"),
    ("Pacific Beverage Distributors (fictitious)", "Alcohol"),
]
FOOD_SPLIT = {"Produce": 0.24, "Seafood": 0.30, "Meat": 0.26, "Dairy": 0.10, "Dry goods": 0.10}


def season(d):
    return 1.10 if d.month in (7, 8) else 0.93 if d.month == 9 else 0.97


def price(item, week):
    p = ITEM[item][2]
    return 26.00 if item == "Ahi Poke Bowl" and week >= POKE_PRICE_WEEK else p


def money_text(x):
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def main():
    for p in (RAW, LATE, TESTS):
        p.mkdir(parents=True, exist_ok=True)
        for old in p.glob("*.csv"):
            old.unlink()

    days = [START + timedelta(days=i) for i in range(WEEKS * 7)]
    summary, pos_rows, sales_by_day = [], {}, {}
    food_cost_by_week, bev_cost_by_week, alc_cost_by_week = {}, {}, {}

    for d in days:
        week = (d - START).days // 7 + 1
        covers = round(240 * WEEKDAY_FACTOR[d.weekday()] * season(d) * random.uniform(0.92, 1.08))
        if d == OVERSTAFF_DAY:
            covers = round(covers * 0.86)                       # the cancelled group: a slow night
        rows, net = [], 0.0
        for group, (items, per_cover) in GROUPS.items():
            weights = [ITEM[i][3] for i in items]
            if group == "main" and week >= POKE_PRICE_WEEK:
                weights[items.index("Ahi Poke Bowl")] *= 0.84   # fewer poke bowls after the price rise
            total_qty = covers * per_cover
            for item, w in zip(items, weights):
                qty = max(0, round(total_qty * w / sum(weights) * random.uniform(0.85, 1.15)))
                amt = round(qty * price(item, week), 2)
                rows.append([d, item, ITEM[item][1], qty, amt, "N"])
                net += amt
                cost = qty * price(item, week) * ITEM[item][4]
                bucket = {"Food": food_cost_by_week, "Beverage": bev_cost_by_week, "Alcohol": alc_cost_by_week}[ITEM[item][1]]
                bucket[week] = bucket.get(week, 0) + cost
        voids = {}
        for _ in range(random.choice([0, 1, 1, 2, 3])):          # a few voids each day, one row per item like a real product mix
            item = random.choice(MENU)[0]
            voids[item] = voids.get(item, 0) + 1
        for item, n in voids.items():
            amt = -round(n * price(item, week), 2)
            rows.append([d, item, ITEM[item][1], -n, amt, "Y"])
            net += amt
        net = round(net, 2)
        checks = round(covers / random.uniform(2.3, 2.7))
        summary.append([d.isoformat(), f"{net:.2f}", covers, checks])
        pos_rows[d] = rows
        sales_by_day[d] = (net, covers)

    # POS product-mix exports: one file per week, format changes at the POS update
    for w in range(1, WEEKS + 1):
        wk_days = days[(w - 1) * 7: w * 7]
        old_style = w < FORMAT_SWITCH_WEEK
        name = f"pos_productmix_{wk_days[0].isoformat()}_to_{wk_days[-1].isoformat()}.csv"
        with open(RAW / name, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["Business Date", "Item Name", "Category", "Qty Sold", "Net Sales", "Void"])
            for d in wk_days:
                if d == MISSING_DAY:
                    continue
                for (dd, item, cat, qty, amt, void) in pos_rows[d]:
                    wr.writerow([dd.strftime("%m/%d/%Y") if old_style else dd.isoformat(), item, cat, qty,
                                 money_text(amt) if old_style else f"{amt:.2f}", void])
            if w == DUP_WEEK:                                   # re-exported Wednesday appended again
                for (dd, item, cat, qty, amt, void) in pos_rows[wk_days[DUP_DAY]]:
                    wr.writerow([dd.isoformat(), item, cat, qty, f"{amt:.2f}", void])
    with open(LATE / f"pos_productmix_{MISSING_DAY.isoformat()}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["Business Date", "Item Name", "Category", "Qty Sold", "Net Sales", "Void"])
        for (dd, item, cat, qty, amt, void) in pos_rows[MISSING_DAY]:
            wr.writerow([dd.isoformat(), item, cat, qty, f"{amt:.2f}", void])

    with open(RAW / "pos_daily_summary.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "net_sales", "covers", "checks"])
        wr.writerows(summary)

    # Time clock: shifts scale with covers; Oct 6 got two extra PM servers for the group that cancelled
    punches = []
    by_role = {}
    for s in STAFF:
        by_role.setdefault(s[2], []).append(s)
    for d in days:
        covers = sales_by_day[d][1]
        rot = d.toordinal()
        for role, shifts in SHIFTS.items():
            people = by_role[role]
            for si, (start_h, length, base, per100) in enumerate(shifts):
                n = base + round(max(0, covers - 160) / 100 * per100)
                if d == OVERSTAFF_DAY and role == "Server" and start_h == 16:
                    n += 2
                n = min(n, len(people))
                for k in range(n):
                    emp = people[(rot + k + si * 3) % len(people)]
                    start = datetime(d.year, d.month, d.day) + timedelta(hours=start_h + random.choice([-0.25, 0, 0, 0.1]))
                    end = start + timedelta(hours=length + random.uniform(-0.3, 0.5))
                    punches.append([emp, start, end])
    with open(RAW / "timeclock_export.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["Employee", "Emp ID", "Job", "Clock In", "Clock Out", "Pay Rate"])
        forgot_done = False
        for i, (emp, start, end) in enumerate(punches):
            name = emp[1].upper() if i % 9 == 0 else emp[1].lower() if i % 13 == 0 else emp[1]      # inconsistent casing
            out = end
            out_text = out.strftime("%m/%d/%Y %H:%M")
            if out.date() > start.date():                         # the clock's midnight bug: keeps the clock-in date
                out_text = start.strftime("%m/%d/%Y") + out.strftime(" %H:%M")
            if not forgot_done and start.date() == date(2026, 10, 8) and emp[2] == "Line Cook" and start.hour >= 15:
                out_text, forgot_done = "", True                  # forgot to clock out
            row = [name, emp[0], emp[2], start.strftime("%m/%d/%Y %H:%M"), out_text, f"{emp[3]:.2f}"]
            wr.writerow(row)
            if i == 777:
                wr.writerow(row)                                  # duplicated punch

    # Purchases (deliveries) and weekly inventory counts
    purchases, inventory = [], []
    inv = {c: v for c, v in {"Produce": 2200, "Seafood": 3100, "Meat": 2800, "Dairy": 900, "Dry goods": 1900,
                              "Beverage": 1500, "Alcohol": 6200}.items()}
    for w in range(1, WEEKS + 1):
        wk_days = days[(w - 1) * 7: w * 7]
        cogs = {c: food_cost_by_week[w] * s for c, s in FOOD_SPLIT.items()}
        cogs["Beverage"], cogs["Alcohol"] = bev_cost_by_week[w], alc_cost_by_week[w]
        if w >= PRODUCE_SPIKE_WEEK:
            cogs["Produce"] *= 1.18                               # same produce, higher prices
        for vendor, cat in VENDORS:
            new_inv = inv[cat] * random.uniform(0.93, 1.07)
            bought = cogs[cat] + new_inv - inv[cat]               # purchases = usage + change in inventory
            n_deliveries = 3 if cat in ("Produce", "Seafood") else 2 if cat in ("Meat", "Dairy") else 1
            for k in range(n_deliveries):
                dd = wk_days[[0, 2, 4][k] if n_deliveries == 3 else [1, 4][k] if n_deliveries == 2 else 2]
                purchases.append([dd.isoformat(), vendor, cat, f"{bought / n_deliveries:.2f}"])
            inv[cat] = new_inv
            inventory.append([wk_days[-1].isoformat(), cat, f"{new_inv:.2f}"])
    with open(RAW / "purchases.csv", "w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["delivery_date", "vendor", "category", "amount"]); wr.writerows(purchases)
    with open(RAW / "inventory_counts.csv", "w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["count_date", "category", "value"])
        wr.writerow([(START - timedelta(days=1)).isoformat(), "ALL_OPENING", ""])          # header-ish junk row a real export has
        for c, v in {"Produce": 2200, "Seafood": 3100, "Meat": 2800, "Dairy": 900, "Dry goods": 1900, "Beverage": 1500, "Alcohol": 6200}.items():
            wr.writerow([(START - timedelta(days=1)).isoformat(), c, f"{v:.2f}"])           # opening count
        wr.writerows(inventory)

    facts = {
        "latest_week_start": days[-7].isoformat(),
        "overstaffed_day": OVERSTAFF_DAY.isoformat(),
        "produce_spike_from_week_start": (START + timedelta(weeks=PRODUCE_SPIKE_WEEK - 1)).isoformat(),
        "poke_price_change_week_start": (START + timedelta(weeks=POKE_PRICE_WEEK - 1)).isoformat(),
        "missing_day": MISSING_DAY.isoformat(),
        "duplicated_day": days[(DUP_WEEK - 1) * 7 + DUP_DAY].isoformat(),
        "forgot_clock_out_day": "2026-10-08",
        "daily_summary_net_sales_total": round(sum(v[0] for v in sales_by_day.values()), 2),
    }
    (TESTS / "known_facts.json").write_text(json.dumps(facts, indent=1))
    print(f"{len(list(RAW.glob('*.csv')))} raw files, {len(punches)} shifts, {len(purchases)} deliveries; "
          f"sales ${facts['daily_summary_net_sales_total']:,.0f} over {len(days)} days")


if __name__ == "__main__":
    main()
