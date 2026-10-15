-- Step 1: load the raw exports exactly as they are (everything as text), then clean them.
-- {RAW} is replaced with the raw-data folder by run.py. Each table keeps a note of what was fixed,
-- so the data-quality report can say exactly what the cleaning changed.

-- ── POS product mix: one row per item per day (voids are separate negative rows) ──
CREATE OR REPLACE TABLE pos_raw AS
SELECT *, row_number() OVER () AS raw_row
FROM read_csv('{RAW}/pos_productmix_*.csv', all_varchar = true, filename = true, header = true);

CREATE OR REPLACE TABLE pos_parsed AS
SELECT
    -- the POS changed date format mid-period: accept both
    coalesce(try_strptime("Business Date", '%m/%d/%Y'), try_strptime("Business Date", '%Y-%m-%d'))::DATE AS business_date,
    trim("Item Name")                                                        AS item,
    trim("Category")                                                         AS category,
    try_cast("Qty Sold" AS INTEGER)                                          AS qty,
    -- older exports wrote amounts as text like "$1,234.50" or "-$16.00"
    try_cast(replace(replace(trim("Net Sales"), '$', ''), ',', '') AS DECIMAL(12, 2)) AS net_sales,
    upper(trim("Void")) = 'Y'                                                AS is_void,
    "Business Date" LIKE '%/%'                                               AS old_date_format,
    "Net Sales" LIKE '%$%'                                                   AS text_amount,
    filename,
    raw_row
FROM pos_raw;

-- A product mix has exactly one row per date + item + void flag. A second identical row is a re-export.
CREATE OR REPLACE TABLE pos_duplicates AS
SELECT business_date, item, is_void, count(*) AS copies, count(DISTINCT (qty, net_sales)) AS distinct_values
FROM pos_parsed
GROUP BY ALL
HAVING count(*) > 1;

CREATE OR REPLACE TABLE pos AS
SELECT * EXCLUDE (rn)
FROM (SELECT *, row_number() OVER (PARTITION BY business_date, item, is_void ORDER BY raw_row) AS rn FROM pos_parsed)
WHERE rn = 1;

-- ── POS daily summary: the POS's own total per day, used to check the product mix adds up ──
CREATE OR REPLACE TABLE pos_summary AS
SELECT CAST(date AS DATE) AS business_date, CAST(net_sales AS DECIMAL(12, 2)) AS net_sales,
       CAST(covers AS INTEGER) AS covers, CAST(checks AS INTEGER) AS checks
FROM read_csv('{RAW}/pos_daily_summary.csv', all_varchar = true, header = true);

-- ── Time clock ──
CREATE OR REPLACE TABLE punches_raw AS
SELECT *, row_number() OVER () AS raw_row
FROM read_csv('{RAW}/timeclock_export.csv', all_varchar = true, header = true);

CREATE OR REPLACE TABLE punches_parsed AS
SELECT
    trim("Emp ID")                                          AS emp_id,
    -- names are typed inconsistently (MARCUS TANAKA, marcus tanaka): standardize to "Marcus Tanaka"
    list_aggregate(list_transform(string_split(lower(trim("Employee")), ' '),
                                  w -> upper(w[1]) || w[2:]), 'string_agg', ' ') AS employee,
    trim("Job")                                             AS role,
    strptime("Clock In", '%m/%d/%Y %H:%M')                  AS clock_in,
    try_strptime(nullif(trim("Clock Out"), ''), '%m/%d/%Y %H:%M') AS clock_out_as_exported,
    CAST("Pay Rate" AS DECIMAL(8, 2))                       AS rate,
    raw_row
FROM punches_raw;

CREATE OR REPLACE TABLE punch_duplicates AS
SELECT emp_id, clock_in, count(*) AS copies FROM punches_parsed GROUP BY ALL HAVING count(*) > 1;

CREATE OR REPLACE TABLE punches_dedup AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY emp_id, clock_in ORDER BY raw_row) AS rn FROM punches_parsed)
WHERE rn = 1;

-- Typical shift length per role and shift (lunch before 2pm, dinner after), used only to estimate missing clock-outs.
CREATE OR REPLACE TABLE typical_shift AS
SELECT role, hour(clock_in) < 14 AS lunch,
       median(epoch(CASE WHEN clock_out_as_exported < clock_in THEN clock_out_as_exported + INTERVAL 1 DAY
                         ELSE clock_out_as_exported END - clock_in) / 3600.0) AS hours
FROM punches_dedup WHERE clock_out_as_exported IS NOT NULL
GROUP BY ALL;

CREATE OR REPLACE TABLE shifts AS
SELECT
    p.emp_id, p.employee, p.role, p.rate, p.clock_in,
    CAST(p.clock_in AS DATE)                                    AS business_date,
    CASE WHEN hour(p.clock_in) < 14 THEN 'Lunch' ELSE 'Dinner' END AS daypart,
    -- the clock keeps the clock-in date for shifts that end after midnight: add the missing day
    CASE WHEN p.clock_out_as_exported IS NULL THEN NULL
         WHEN p.clock_out_as_exported < p.clock_in THEN p.clock_out_as_exported + INTERVAL 1 DAY
         ELSE p.clock_out_as_exported END                       AS clock_out,
    p.clock_out_as_exported IS NOT NULL AND p.clock_out_as_exported < p.clock_in AS midnight_fixed,
    p.clock_out_as_exported IS NULL                             AS clock_out_missing,
    round(CASE WHEN p.clock_out_as_exported IS NULL THEN t.hours
               ELSE epoch((CASE WHEN p.clock_out_as_exported < p.clock_in THEN p.clock_out_as_exported + INTERVAL 1 DAY
                                ELSE p.clock_out_as_exported END) - p.clock_in) / 3600.0 END, 2) AS hours
FROM punches_dedup p
LEFT JOIN typical_shift t ON t.role = p.role AND t.lunch = (hour(p.clock_in) < 14);

-- Overtime: hours past 40 in a Monday-Sunday week are paid at 1.5x (Hawaii follows the federal 40-hour rule).
CREATE OR REPLACE TABLE shifts_paid AS
WITH running AS (
    SELECT *, date_trunc('week', business_date) AS week_start,
           sum(hours) OVER (PARTITION BY emp_id, date_trunc('week', business_date) ORDER BY clock_in
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS week_hours_so_far
    FROM shifts)
SELECT *,
       greatest(0, least(hours, week_hours_so_far - 40))                    AS overtime_hours,
       round(rate * (hours + 0.5 * greatest(0, least(hours, week_hours_so_far - 40))), 2) AS wages
FROM running;

-- ── Purchases and inventory counts ──
CREATE OR REPLACE TABLE purchases AS
SELECT CAST(delivery_date AS DATE) AS delivery_date, trim(vendor) AS vendor, trim(category) AS category,
       CAST(amount AS DECIMAL(12, 2)) AS amount
FROM read_csv('{RAW}/purchases.csv', all_varchar = true, header = true);

CREATE OR REPLACE TABLE inventory AS
SELECT CAST(count_date AS DATE) AS count_date, trim(category) AS category, CAST(value AS DECIMAL(12, 2)) AS value
FROM read_csv('{RAW}/inventory_counts.csv', all_varchar = true, header = true)
WHERE nullif(trim(value), '') IS NOT NULL AND category NOT LIKE 'ALL_%';     -- skip the export's blank summary row
