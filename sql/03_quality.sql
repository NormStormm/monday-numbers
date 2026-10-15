-- Step 3: the data-quality gate. Every check returns rows: severity, check, detail.
--   BLOCK  = the numbers would be wrong: nothing is published until it's fixed
--   WARN   = published, with the problem listed
--   FIXED  = the cleaning step corrected it automatically (listed so nothing is silently changed)

CREATE OR REPLACE TABLE quality AS

-- BLOCK: a day the POS says had sales, but no product-mix rows (a missing export)
SELECT 'BLOCK' AS severity, 'Missing POS day' AS check_name,
       strftime(s.business_date, '%a %b %d') || ': POS daily summary shows $' || format('{:,.2f}', s.net_sales)
       || ' in sales, but the product-mix export has no rows for this day' AS detail
FROM pos_summary s
WHERE s.business_date NOT IN (SELECT business_date FROM pos)

UNION ALL
-- BLOCK: product mix doesn't add up to the POS's own daily total (more than 0.5% apart)
SELECT 'BLOCK', 'Sales don''t reconcile',
       strftime(s.business_date, '%a %b %d') || ': product mix $' || format('{:,.2f}', d.net_sales)
       || ' vs POS summary $' || format('{:,.2f}', s.net_sales)
FROM pos_summary s JOIN daily d USING (business_date)
WHERE abs(d.net_sales - s.net_sales) > 0.005 * s.net_sales

UNION ALL
-- BLOCK: re-exported rows that disagree with the original (can't tell which one is right)
SELECT 'BLOCK', 'Conflicting duplicate rows',
       strftime(business_date, '%a %b %d') || ' ' || item || ': ' || copies || ' rows with different amounts'
FROM pos_duplicates WHERE distinct_values > 1

UNION ALL
-- BLOCK: an inventory count is missing, so cost of goods can't be calculated for that week
SELECT 'BLOCK', 'Missing inventory count', 'Week of ' || strftime(week_start, '%b %d') || ' ' || category
FROM cogs_weekly WHERE opening_inventory IS NULL OR closing_inventory IS NULL

UNION ALL
-- BLOCK: a day with negative sales
SELECT 'BLOCK', 'Negative sales day', strftime(business_date, '%a %b %d') || ': $' || format('{:,.2f}', net_sales)
FROM daily WHERE net_sales < 0

UNION ALL
-- WARN: forgot to clock out (hours estimated from that role's typical shift)
SELECT 'WARN', 'Missing clock-out',
       employee || ' (' || role || '), ' || strftime(clock_in, '%a %b %d %H:%M') || ': hours estimated at '
       || hours || ' from a typical ' || lower(daypart) || ' shift. Have the manager confirm.'
FROM shifts WHERE clock_out_missing

UNION ALL
-- WARN: shifts long enough to be a punch mistake
SELECT 'WARN', 'Very long shift', employee || ' ' || strftime(clock_in, '%a %b %d') || ': ' || hours || ' hours'
FROM shifts WHERE hours > 13

UNION ALL
SELECT 'FIXED', 'Duplicate POS rows removed',
       count(*) || ' rows re-exported for ' || string_agg(DISTINCT strftime(business_date, '%a %b %d'), ', ')
       || ' (identical copies, kept one)'
FROM pos_duplicates WHERE distinct_values = 1 HAVING count(*) > 0

UNION ALL
SELECT 'FIXED', 'Duplicate time punches removed',
       count(*) || ' duplicated punch(es), e.g. ' || min(emp_id) || ' on ' || strftime(min(clock_in), '%a %b %d')
FROM punch_duplicates HAVING count(*) > 0

UNION ALL
SELECT 'FIXED', 'Overnight clock-outs corrected',
       count(*) || ' shifts ended after midnight but were exported with the clock-in date; one day added'
FROM shifts WHERE midnight_fixed HAVING count(*) > 0

UNION ALL
SELECT 'FIXED', 'POS formats standardized',
       sum(old_date_format::INT) || ' rows used MM/DD/YYYY dates and ' || sum(text_amount::INT)
       || ' used "$1,234.50" text amounts (before the POS update); converted'
FROM pos_parsed HAVING sum(old_date_format::INT) > 0

UNION ALL
SELECT 'FIXED', 'Employee names standardized',
       count(DISTINCT trim("Employee")) - count(DISTINCT lower(trim("Employee"))) || ' name spellings differed only in capitalization'
FROM punches_raw HAVING count(DISTINCT trim("Employee")) > count(DISTINCT lower(trim("Employee")));
